#!/usr/bin/env python3
"""T1 calibration ladder G0..G5 on the dev model, cheapest signal first. Writes the JSON each gate
consumes, then runs the gates. Two stages so vLLM and the nnsight copy never share the GPU:

  python -m calibrate.t1_ladder --stage vllm      # vLLM up: greedy G0 completion + greedy G1 transcript
  python -m calibrate.t1_ladder --stage nnsight   # vLLM down: nnsight replay, SAE, G2..G5, run gates

Everything is T=0. Outputs under --out (default t1/): features/, transcripts/, replayed/.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
CFG = ROOT / "config"
MODELS = yaml.safe_load((CFG / "models.yaml").read_text())
CAL = MODELS.get("calibration", {})

# ---------------------------------------------------------------- calibration texts (fixed, hashed)
G0_MESSAGES = [
    {"role": "system", "content": "You are a concise assistant."},
    {"role": "user", "content": "List three prime numbers greater than 10, comma-separated, nothing else."},
]

CODE_TEXT = '''def fibonacci(n):
    """Return the nth Fibonacci number."""
    if n < 2:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b


def is_prime(n):
    """Return True if n is prime."""
    if n < 2:
        return False
    for d in range(2, int(n ** 0.5) + 1):
        if n % d == 0:
            return False
    return True
'''

PROSE_TEXT = '''The river had risen overnight, and by morning the lower fields were a sheet of brown water. Farmers
stood on the embankment in small groups, saying little. Someone remarked that the last flood like this had
come thirty years ago, and that the bridge had held then too. The children, who had been promised a day off
school, were delighted, and ran along the wall shouting at the ducks that paddled where the road had been.
By afternoon the rain had stopped and a thin light came through the clouds over the hills.
'''

MENTION_TEXT = '''Yesterday I spent the afternoon writing a small Python function for a friend. It computes Fibonacci
numbers and has a short docstring explaining what it returns. We talked about programming for a while, about
how satisfying it is when a function finally works, and then we went for a walk by the river.
'''

CALIB_PROSE = PROSE_TEXT * 3 + '''Economists have long debated whether the observed decline in the labor share of income reflects
technological change, the growth of market power, or measurement artifacts in how capital is counted. The
evidence is mixed. Studies using firm-level data tend to attribute much of the shift to the rise of large,
highly productive firms, while aggregate studies emphasize the falling price of investment goods.
In either case the trend appears in most advanced economies, which argues against purely national explanations.
'''

STEER_PROMPT = [{"role": "user", "content": "Write something."}]


def _dump(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2))


# ================================================================ stage 1: vLLM (serving path)
def _vllm_complete(base_url, model, prompt, max_tokens, logprobs=True):
    import urllib.request
    body = {"model": model, "prompt": prompt, "temperature": 0.0, "max_tokens": max_tokens,
            "logprobs": 1 if logprobs else None, "return_token_ids": True, "add_special_tokens": True,
            "seed": 0}
    req = urllib.request.Request(base_url.rstrip("/") + "/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + os.environ.get("LOCAL_API_KEY", "x")})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode())
    ch = d["choices"][0]
    prompt_ids = ch.get("prompt_token_ids") or d.get("prompt_token_ids")
    gen_ids = ch.get("token_ids")
    lp = ch.get("logprobs") or {}
    return {"text": ch["text"], "prompt_token_ids": prompt_ids, "token_ids": gen_ids,
            "token_logprobs": lp.get("token_logprobs"), "finish_reason": ch.get("finish_reason"), "raw_keys": list(ch.keys())}


def _wait_server(base_url, timeout=900):
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=5)
            return True
        except Exception:
            time.sleep(5)
    return False


def stage_vllm(out):
    from model_io.gemma2 import serialize_messages, prompt_hash
    ep = MODELS["endpoint"]
    base, model = ep["base_url"], ep["served_model_name"]
    assert _wait_server(base), f"vLLM not reachable at {base}"
    # G0: one greedy completion on the calibration prompt
    p0 = serialize_messages(G0_MESSAGES, add_generation_prompt=True)
    r0 = _vllm_complete(base, model, p0, max_tokens=CAL.get("g0_max_tokens", 32))
    if r0["prompt_token_ids"] is None or r0["token_ids"] is None:
        raise RuntimeError(f"vLLM did not return token ids (return_token_ids unsupported?): keys={r0['raw_keys']}")
    # G1: greedy generation of the decision turn of the fixture transcript
    from generate.extract_transcripts import fixture_transcript
    row = fixture_transcript()
    prefix = row["messages"][:row["decision_point"]]
    p1 = serialize_messages(prefix, add_generation_prompt=True)
    r1 = _vllm_complete(base, model, p1, max_tokens=CAL.get("g1_max_tokens", 96))
    _dump(Path(out) / "features" / "t1_vllm.json", {
        "g0": {"prompt": p0, "prompt_hash": prompt_hash(G0_MESSAGES), **r0},
        "g1": {"prompt": p1, "prompt_hash": prompt_hash(prefix), "decision_point": row["decision_point"],
               "uid": row["uid"], **r1},
    })
    print("G0 vLLM:", repr(r0["text"][:80]), "| prompt_ids", len(r0["prompt_token_ids"]), "| gen", len(r0["token_ids"]))
    print("G1 vLLM:", repr(r1["text"][:80]), "| gen", len(r1["token_ids"]), "finish", r1["finish_reason"])


# ================================================================ stage 2: nnsight (observation path)
EOS_IDS = (1, 107)   # <eos>, <end_of_turn>


def _strip_eos(ids, lps):
    ids = list(ids)
    lps = list(lps) if lps else None
    while ids and ids[-1] in EOS_IDS:
        ids.pop()
        if lps:
            lps.pop()
    return ids, lps


def stage_nnsight(out, gates_only=None):
    import numpy as np
    import torch
    from model_io.gemma2 import apply_to_tokenizer
    from replay.modelload import load_target, HOOK_READERS, hook_reader
    from replay.hooks import teacher_forced_forward, greedy_generate, build_input_ids
    from replay.sae import load_sae, sae_health, hook_identification_report, encode_dense, fetch_neuronpedia_labels
    from gates.g3_feature_known_answer import auroc
    out = Path(out)
    feat = out / "features"
    v = json.loads((feat / "t1_vllm.json").read_text())
    lm = load_target("target")
    tok = lm.tokenizer
    print(f"model loaded: layers={lm.n_layers} d_model={lm.d_model} hook layer={lm.layer}")

    # ---- G0: prompt identity + computation identity
    ser_ids = apply_to_tokenizer(tok, G0_MESSAGES, add_generation_prompt=True)
    nn_gen = greedy_generate(lm, ser_ids, max_new_tokens=CAL.get("g0_max_tokens", 32))
    vllm_gen, _ = _strip_eos(v["g0"]["token_ids"], None)
    _dump(feat / "model_checksum.json", {
        "serializer_prompt_ids": ser_ids, "vllm_prompt_ids": v["g0"]["prompt_token_ids"],
        "nnsight_prompt_ids": ser_ids, "vllm_gen_ids": vllm_gen, "nnsight_gen_ids": nn_gen,
        "vllm_text": v["g0"]["text"], "nnsight_text": tok.decode(nn_gen)})
    print("G0 nnsight:", repr(tok.decode(nn_gen)[:80]))

    # ---- G1: exact replay of the greedy transcript, through the TEXT path (the real replay path)
    from generate.extract_transcripts import fixture_transcript
    row = fixture_transcript()
    dp = v["g1"]["decision_point"]
    s_ids, s_lps = _strip_eos(v["g1"]["token_ids"], v["g1"]["token_logprobs"])
    gen_text = tok.decode(s_ids)
    msgs = row["messages"][:dp] + [{"role": "assistant", "content": gen_text}]
    row = dict(row, messages=msgs, scored_message_index=dp, uid=row["uid"] + "/t1greedy")
    row["tokens"] = {"sampled_ids": s_ids, "sampled_logprobs": s_lps}
    fr = teacher_forced_forward(lm, msgs, capture_residual=True)
    s, e = fr.assistant_span
    span_ids = fr.token_ids[s:e]
    g1_diag = {"sampled_len": len(s_ids), "span_len": e - s, "span_ids_equal_sampled": span_ids == s_ids}
    if span_ids != s_ids:
        # find first divergence for the report
        k = next((i for i, (a, b) in enumerate(zip(span_ids, s_ids)) if a != b), min(len(span_ids), len(s_ids)))
        g1_diag["first_divergence"] = {"k": k, "span": span_ids[max(0, k - 3):k + 3], "sampled": s_ids[max(0, k - 3):k + 3],
                                       "span_txt": tok.decode(span_ids[max(0, k - 3):k + 3]),
                                       "sampled_txt": tok.decode(s_ids[max(0, k - 3):k + 3])}
    # raw-id diagnostic: teacher-force EXACTLY prefix+sampled ids (isolates numerics from retokenization)
    prefix_ids = apply_to_tokenizer(tok, msgs[:-1], add_generation_prompt=True)
    raw = teacher_forced_forward(lm, None, capture_residual=False, input_ids=prefix_ids + s_ids,
                                 span=(len(prefix_ids), len(prefix_ids) + len(s_ids)))
    rs = len(prefix_ids)
    raw_pred = [raw.logits_argmax[rs - 1 + k] for k in range(len(s_ids))]
    g1_diag["raw_ids_exact_match"] = sum(a == b for a, b in zip(raw_pred, s_ids)) / max(1, len(s_ids))
    g1_diag["raw_ids_max_logprob_gap"] = (max(abs(a - b) for a, b in zip(raw.input_logprobs[rs:rs + len(s_ids)], s_lps))
                                          if s_lps else None)
    # contract arrays, continuation-relative and equal length
    row["tokens"].update({
        "ids": fr.token_ids, "assistant_span": [s, e], "generated_ids": s_ids,
        "replay_predicted_ids": [fr.logits_argmax[s - 1 + k] for k in range(e - s)],
        "replay_logprob": fr.input_logprobs[s:e], "generation_logprob": s_lps,
        "prompt_token_count": s, "decision_token_position": s, "assistant_token_count": e - s})
    tdir, rdir = out / "transcripts" / "arm_a", out / "replayed" / "arm_a"
    tdir.mkdir(parents=True, exist_ok=True); rdir.mkdir(parents=True, exist_ok=True)
    gen_row = {k: val for k, val in row.items() if k != "tokens"}
    gen_row["tokens"] = {"sampled_ids": s_ids, "sampled_logprobs": s_lps}
    (tdir / "t1.jsonl").write_text(json.dumps(gen_row) + "\n")
    (rdir / "t1.jsonl").write_text(json.dumps({"uid": row["uid"], "tokens": row["tokens"]}) + "\n")
    _dump(feat / "g1_diagnostics.json", g1_diag)
    print("G1 diag:", g1_diag)

    # ---- G2: hook identification on calibration text (prose + code), chosen hook AND decoys
    sae = load_sae()
    hooks = [MODELS["sae"]["hook_point"]] + list(MODELS["sae"].get("hook_candidates", []))
    readers = {h: hook_reader(h) for h in hooks}
    cal_ids = _pile_ids(tok, CAL.get("g2_max_tokens", 1024)) or \
        tok(CALIB_PROSE + "\n" + CODE_TEXT, add_special_tokens=True)["input_ids"][:CAL.get("g2_max_tokens", 1024)]
    frc = teacher_forced_forward(lm, None, capture_residual=True, input_ids=cal_ids,
                                 extra_hooks=[r for r in readers.values() if r != "block_output"])
    res_by_hook = {}
    for h, r in readers.items():
        res_by_hook[h] = frc.residual if r == "block_output" else frc.extra[r]
    rep = hook_identification_report(lm, sae, res_by_hook, feat / "sae_health.json")
    print("G2 chosen:", {k: (round(val, 4) if isinstance(val, float) else val) for k, val in rep["chosen"].items()})
    for c in rep["candidates"]:
        print("   decoy:", c["hook"], "ve=%.3f l0=%.1f" % (c["var_explained"], c["l0"]))

    # ---- G3: known code feature discriminates code from prose (position-level AUROC)
    fidx = CAL.get("code_feature_index")
    if fidx is None:
        _dump(feat / "known_answer_report.json", {"error": "calibration.code_feature_index not set in models.yaml"})
        print("G3: SKIPPED (no code_feature_index)")
    else:
        def acts_on(text):
            ids = tok(text, add_special_tokens=True)["input_ids"]
            f = teacher_forced_forward(lm, None, capture_residual=True, input_ids=ids)
            return encode_dense(sae, f.residual)[1:, fidx]          # drop BOS
        a_code, a_prose, a_mention = acts_on(CODE_TEXT), acts_on(PROSE_TEXT), acts_on(MENTION_TEXT)
        a_planted = acts_on(PROSE_TEXT + "\n" + CODE_TEXT)
        rep3 = {"feature": fidx, "concept_positions": a_code.tolist(), "other_positions": a_prose.tolist(),
                "planted_secrecy_activation": float(a_planted.mean()),       # planted-concept analog: code planted into prose
                "baseline_secrecy_activation": float(a_prose.mean()),
                "mention_behavior_activation": float(a_mention.mean()),      # topic-vs-behavior analog: prose ABOUT code
                "control_behavior_activation": float(a_prose.mean()),
                "auroc": auroc(a_code.tolist(), a_prose.tolist()),
                "frac_active": {"code": float((a_code > 0).mean()), "prose": float((a_prose > 0).mean()),
                                "mention": float((a_mention > 0).mean())},
                "note": "planted/mention fields reuse the code feature (code planted in prose; prose that talks about code)"}
        extra = {}
        for fx in CAL.get("extra_feature_indices", []) or []:
            def acts_x(text, fx=fx):
                ids = tok(text, add_special_tokens=True)["input_ids"]
                f = teacher_forced_forward(lm, None, capture_residual=True, input_ids=ids)
                return encode_dense(sae, f.residual)[1:, fx]
            c, pr = acts_x(CODE_TEXT), acts_x(PROSE_TEXT)
            extra[str(fx)] = {"auroc": auroc(c.tolist(), pr.tolist()), "code_active": float((c > 0).mean()),
                              "prose_active": float((pr > 0).mean())}
        rep3["extra_features"] = extra
        _dump(feat / "known_answer_report.json", rep3)
        try:
            fetch_neuronpedia_labels([fidx] + list(CAL.get("extra_feature_indices", []) or []), feat / "feature_labels.json")
        except Exception as ex:
            print("neuronpedia fetch failed:", ex)
        print("G3: auroc=%.3f code_active=%.2f prose_active=%.2f mention_active=%.2f" % (
            rep3["auroc"], rep3["frac_active"]["code"], rep3["frac_active"]["prose"], rep3["frac_active"]["mention"]))

        # ---- G4: steer along the code feature's decoder direction; readout = P(next token opens code)
        W = sae.W_dec[fidx].detach().float().cpu().numpy()
        p_ids = apply_to_tokenizer(tok, STEER_PROMPT, add_generation_prompt=True)
        code_openers = [t for t in ["```", "def", "import", "```python", "class", "#", "print"]]
        opener_ids = sorted({tok(t, add_special_tokens=False)["input_ids"][0] for t in code_openers})
        sweep = yaml.safe_load((CFG / "run.yaml").read_text())["steering"]["sweep"]
        curve, samples = {}, {}
        for strength in sweep:
            f = teacher_forced_forward(lm, None, capture_residual=False, input_ids=p_ids, steer=(W, strength))
            # recompute logprob vector at last position from a fresh forward that returns logits
            # (teacher_forced_forward returns argmax + input logprobs; we need the full last-position distribution)
            curve[str(strength)] = _last_pos_mass(lm, p_ids, (W, strength), opener_ids)
            samples[str(strength)] = tok.decode(greedy_generate(lm, p_ids, max_new_tokens=24,
                                                                 ) ) if strength == 0 else None
        _dump(feat / "steering_report.json", {"feature": fidx, "readout": "P(next token in code openers)",
                                              "opener_ids": opener_ids, "curve": curve, "samples": samples})
        print("G4 curve:", {k: round(val, 4) for k, val in curve.items()})

    # ---- G5: oracle. Residuals are captured UNADAPTED first; the LoRA is loaded last of all.
    from replay.oracle import load_oracle, oracle_calibration_report, gather_residuals
    samples = gather_residuals(lm, tok, {"code": CODE_TEXT, "prose": PROSE_TEXT})
    try:
        rep5p = oracle_calibration_report(load_oracle("patchscopes", lm=lm), samples, feat / "oracle_calibration_patchscopes.json")
        print("G5 (patchscopes baseline):", {k: round(rep5p[k], 3) for k in ("accuracy", "confab_rate")})
    except Exception as ex:
        import traceback; traceback.print_exc()
        print("G5 patchscopes baseline failed:", ex)
    try:
        oracle = load_oracle("karvonen", lm=lm)
        rep5 = oracle_calibration_report(oracle, samples, feat / "oracle_calibration.json")
        print("G5 (karvonen):", {k: round(rep5[k], 3) for k in ("accuracy", "confab_rate")})
    except Exception as ex:
        import traceback; traceback.print_exc()
        _dump(feat / "oracle_calibration.json", {"accuracy": 0.0, "confab_rate": 1.0, "error": str(ex)[:300]})
        print("G5: FAILED to run karvonen oracle:", ex)

    # ---- run the gates on what we wrote
    import subprocess
    cmd = [sys.executable, "-m", "gates.run_gates", "--transcripts", str(out / "transcripts"),
           "--replayed", str(out / "replayed"), "--features", str(feat), "--gates", gates_only or "G0,G1,G2,G3,G4,G5"]
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT)


def _pile_ids(tok, n_tokens, n_docs=12):
    """A slice of the SAE's training distribution (models.yaml sae.published.dataset) so the L0
    comparison is apples-to-apples. Returns None if the dataset can't be streamed."""
    pub = MODELS["sae"].get("published", {})
    name = pub.get("dataset")
    if not name:
        return None
    try:
        from datasets import load_dataset
        ds = load_dataset(name, split="train", streaming=True)
        ids = []
        for i, ex in enumerate(ds):
            ids += tok(ex["text"], add_special_tokens=False)["input_ids"]
            if len(ids) >= n_tokens - 1 or i >= n_docs:
                break
        ids = [tok.bos_token_id] + ids[:n_tokens - 1]
        print(f"G2 calibration text: {len(ids)} tokens from {name}")
        return ids
    except Exception as ex:
        print("pile streaming unavailable (%s); using built-in calibration text" % ex)
        return None


def _last_pos_mass(lm, ids, steer, target_ids):
    """Probability mass on target_ids at the last position under steering (through the shared forward)."""
    import torch
    from replay.modelload import residual_module
    from replay.hooks import resid_post, _set_block_output, _val
    import numpy as np
    model, layer = lm.model, residual_module(lm)
    vec, strength = steer
    with model.trace(torch.tensor([ids])):
        stream = resid_post(layer.output)
        v = torch.as_tensor(np.asarray(vec, dtype=np.float32)).to(stream.device, stream.dtype)
        unit = v / (v.norm() + 1e-6)
        mean_norm = stream[0, 1:].float().norm(dim=-1).mean().to(stream.dtype)
        _set_block_output(layer, stream + float(strength) * mean_norm * unit)
        logits = model.output.logits[0, -1].float().save()
    p = torch.softmax(_val(logits), dim=-1)
    return float(p[target_ids].sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["vllm", "nnsight"], required=True)
    ap.add_argument("--out", default="t1")
    ap.add_argument("--gates", default=None)
    a = ap.parse_args()
    if a.stage == "vllm":
        stage_vllm(a.out)
    else:
        stage_nnsight(a.out, a.gates)


if __name__ == "__main__":
    main()
