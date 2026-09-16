#!/usr/bin/env python3
"""T1 calibration ladder G0..G5 on the dev model, cheapest signal first. Writes the JSON each gate
consumes, then runs the gates. Two stages so vLLM and the nnsight copy never share the GPU:

  python -m calibrate.t1_ladder --stage vllm      # vLLM up: greedy G0 completion + greedy G1 transcript
  python -m calibrate.t1_ladder --stage nnsight   # vLLM down: nnsight replay, SAE, G2..G5, run gates
  python -m calibrate.t1_ladder --stage identity  # TransformerLens (no weight processing) vs the saved nnsight tensor
Rules: gates/_common.GATE_RULES_VERSION (see gates/CHANGELOG.md). T1_DTYPE=float32 for the fp32 G1 check.

Everything is T=0. Outputs under --out (default t1/): features/, transcripts/, replayed/.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
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
            "logprobs": 2 if logprobs else None, "return_token_ids": True, "add_special_tokens": True,
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
    margins = None
    if lp.get("top_logprobs"):
        margins = []
        for d in lp["top_logprobs"]:
            vals = sorted((d or {}).values(), reverse=True)
            margins.append(float(vals[0] - vals[1]) if len(vals) >= 2 else None)
    return {"text": ch["text"], "prompt_token_ids": prompt_ids, "token_ids": gen_ids,
            "token_logprobs": lp.get("token_logprobs"), "top2_margin": margins,
            "finish_reason": ch.get("finish_reason"), "raw_keys": list(ch.keys())}


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
    from gates._common import GATE_RULES_VERSION
    from model_io.gemma2 import apply_to_tokenizer
    from replay.modelload import load_target, hook_reader
    from replay.hooks import teacher_forced_forward, greedy_generate, greedy_generate_at_layer
    from replay.sae import (load_sae, sae_health, hook_identification_report, encode_dense, fetch_neuronpedia_labels,
                            jumprelu_integrity, sae_artifact_identity)
    from gates.g3_feature_known_answer import auroc
    out = Path(out)
    feat = out / "features"
    v = json.loads((feat / "t1_vllm.json").read_text())
    lm = load_target("target")
    tok = lm.tokenizer
    dtype = str(next(lm.model.model.parameters()).dtype) if hasattr(lm.model, "model") else "?"
    _dump(feat / "gate_rules.json", {"rules": GATE_RULES_VERSION, "dtype": dtype})
    print(f"model loaded: layers={lm.n_layers} d_model={lm.d_model} hook layer={lm.layer} dtype={dtype} rules={GATE_RULES_VERSION}")

    # ---- G0: prompt identity + computation identity
    ser_ids = apply_to_tokenizer(tok, G0_MESSAGES, add_generation_prompt=True)
    nn_gen = greedy_generate(lm, ser_ids, max_new_tokens=CAL.get("g0_max_tokens", 32))
    vllm_gen, _ = _strip_eos(v["g0"]["token_ids"], None)
    _dump(feat / "model_checksum.json", {
        "rules": GATE_RULES_VERSION, "dtype": dtype,
        "serializer_prompt_ids": ser_ids, "vllm_prompt_ids": v["g0"]["prompt_token_ids"],
        "nnsight_prompt_ids": ser_ids, "vllm_gen_ids": vllm_gen, "nnsight_gen_ids": nn_gen,
        "vllm_text": v["g0"]["text"], "nnsight_text": tok.decode(nn_gen)})
    print("G0 nnsight:", repr(tok.decode(nn_gen)[:80]))

    # ---- G1: exact replay of the greedy transcript through the TEXT path (the real replay path)
    from generate.extract_transcripts import fixture_transcript
    row = fixture_transcript()
    dp = v["g1"]["decision_point"]
    s_ids, s_lps = _strip_eos(v["g1"]["token_ids"], v["g1"]["token_logprobs"])
    margins = (v["g1"].get("top2_margin") or [None] * len(s_ids))[:len(s_ids)]
    gen_text = tok.decode(s_ids)
    msgs = row["messages"][:dp] + [{"role": "assistant", "content": gen_text}]
    row = dict(row, messages=msgs, scored_message_index=dp, uid=row["uid"] + "/t1greedy")
    row["sampling"] = {"temperature": 0.0, "top_p": 1.0}          # G1 reads the criterion from HERE
    row["tokens"] = {"sampled_ids": s_ids, "sampled_logprobs": s_lps, "sampled_top2_margin": margins}
    fr = teacher_forced_forward(lm, msgs, capture_residual=True)
    s, e = fr.assistant_span
    span_ids = fr.token_ids[s:e]
    g1_diag = {"rules": GATE_RULES_VERSION, "dtype": dtype, "sampled_len": len(s_ids), "span_len": e - s,
               "span_ids_equal_sampled": span_ids == s_ids}
    prefix_ids = apply_to_tokenizer(tok, msgs[:-1], add_generation_prompt=True)
    raw = teacher_forced_forward(lm, None, capture_residual=False, input_ids=prefix_ids + s_ids,
                                 span=(len(prefix_ids), len(prefix_ids) + len(s_ids)))
    rs = len(prefix_ids)
    raw_pred = [raw.logits_argmax[rs - 1 + k] for k in range(len(s_ids))]
    g1_diag["raw_ids_exact_match"] = sum(a == b for a, b in zip(raw_pred, s_ids)) / max(1, len(s_ids))
    mism = [k for k, (a, b) in enumerate(zip(raw_pred, s_ids)) if a != b]
    g1_diag["raw_ids_mismatches"] = [{"k": k, "vllm_id": s_ids[k], "vllm_tok": tok.decode([s_ids[k]]),
                                      "replay_argmax_id": raw_pred[k], "replay_argmax_tok": tok.decode([raw_pred[k]]),
                                      "replay_logprob_of_vllm_tok": raw.input_logprobs[rs + k],
                                      "vllm_logprob": (s_lps[k] if s_lps else None),
                                      "generation_top2_margin": margins[k] if k < len(margins) else None,
                                      "replay_top2": raw.logits_top2[rs - 1 + k]} for k in mism[:5]]
    row["tokens"].update({
        "ids": fr.token_ids, "assistant_span": [s, e], "generated_ids": s_ids,
        "replay_predicted_ids": [fr.logits_argmax[s - 1 + k] for k in range(e - s)],
        "replay_top2_ids": [fr.logits_top2[s - 1 + k] for k in range(e - s)],
        "replay_logprob": fr.input_logprobs[s:e], "generation_logprob": s_lps,
        "prompt_token_count": s, "decision_token_position": s, "assistant_token_count": e - s})
    tdir, rdir = out / "transcripts" / "arm_a", out / "replayed" / "arm_a"
    tdir.mkdir(parents=True, exist_ok=True); rdir.mkdir(parents=True, exist_ok=True)
    gen_row = {k: val for k, val in row.items() if k != "tokens"}
    gen_row["tokens"] = {"sampled_ids": s_ids, "sampled_logprobs": s_lps, "sampled_top2_margin": margins}
    (tdir / "t1.jsonl").write_text(json.dumps(gen_row) + "\n")
    (rdir / "t1.jsonl").write_text(json.dumps({"uid": row["uid"], "tokens": row["tokens"]}) + "\n")
    _dump(feat / "g1_diagnostics.json", g1_diag)
    print("G1 diag:", {k: g1_diag[k] for k in ("span_ids_equal_sampled", "raw_ids_exact_match")}, "mismatches:", g1_diag["raw_ids_mismatches"])
    chat_resid = fr.residual

    # ---- G2: hook identification. Per-document Pile slices (own BOS, 1024 ctx, BOS excluded), decoys +
    #      scaled copies, JumpReLU integrity, artifact identity, and the tensor saved for the identity stage.
    sae = load_sae()
    art = sae_artifact_identity(sae)
    print("SAE artifact:", art)
    hooks = [MODELS["sae"]["hook_point"]] + list(MODELS["sae"].get("hook_candidates", []))
    readers = {h: hook_reader(h) for h in hooks}
    docs = _pile_docs(tok, n_docs=CAL.get("g2_n_docs", 16), ctx=CAL.get("g2_ctx", 1024))
    if not docs:
        docs = [tok(CALIB_PROSE, add_special_tokens=True)["input_ids"][:1024], tok(CODE_TEXT, add_special_tokens=True)["input_ids"]]
    pooled = {h: [] for h in hooks}
    per_doc_l0, per_doc_ve = [], []
    for i, ids in enumerate(docs):
        f = teacher_forced_forward(lm, None, capture_residual=True, input_ids=ids,
                                   extra_hooks=[r for r in readers.values() if r != "block_output"])
        for h, r in readers.items():
            pooled[h].append((f.residual if r == "block_output" else f.extra[r])[1:])   # drop BOS per doc
        hd = sae_health(sae, f.residual, skip_bos=True)
        per_doc_l0.append(hd["l0"]); per_doc_ve.append(hd["var_explained"])
        if i == 0:
            np.savez(feat / "identity_input.npz", ids=np.array(ids), resid_post=f.residual.astype(np.float32),
                     resid_pre=f.extra[readers["layers.31.input_resid"]].astype(np.float32))
    res_by_hook = {h: np.concatenate(v, 0) for h, v in pooled.items()}
    chosen_name = MODELS["sae"]["hook_point"]
    for sc in (0.8, 1.2):
        res_by_hook[f"{chosen_name}_x{sc}"] = res_by_hook[chosen_name] * sc
    rep = hook_identification_report(lm, sae, res_by_hook, feat / "sae_health.json", skip_bos=False,
                                     extra_candidates=[f"{chosen_name}_x0.8", f"{chosen_name}_x1.2"])
    jr = jumprelu_integrity(sae, res_by_hook[chosen_name])
    rep.update({"rules": GATE_RULES_VERSION, "dtype": dtype, "per_doc_l0": per_doc_l0, "per_doc_var_explained": per_doc_ve,
                "n_docs": len(docs), "ctx": CAL.get("g2_ctx", 1024), "bos_excluded": True,
                "jumprelu_below_threshold_frac": jr["below_threshold_frac"], "jumprelu_n_active": jr["n_active"],
                "chat_health": sae_health(sae, chat_resid, skip_bos=True), "artifact": art})
    _dump(feat / "sae_health.json", rep)
    pd = sorted(per_doc_l0)
    print("G2 chosen (pooled):", {k: (round(val, 4) if isinstance(val, float) else val) for k, val in rep["chosen"].items()})
    print("   per-doc L0: median=%.1f min=%.1f max=%.1f | jumprelu below-threshold frac=%.4f | chat L0=%.1f" % (
        pd[len(pd) // 2], pd[0], pd[-1], jr["below_threshold_frac"], rep["chat_health"]["l0"]))
    for c in rep["candidates"]:
        print("   decoy:", c["hook"], "ve=%.3f l0=%.1f" % (c["var_explained"], c["l0"]))

    # ---- G3: window-max discrimination on labeled code features
    fidx = CAL.get("code_feature_index")
    feats = [fidx] + list(CAL.get("extra_feature_indices", []) or [])

    def acts_on(text, fx):
        ids = tok(text, add_special_tokens=True)["input_ids"]
        f = teacher_forced_forward(lm, None, capture_residual=True, input_ids=ids)
        return encode_dense(sae, f.residual)[1:, fx]
    per = {}
    for fx in feats:
        c, p, m = acts_on(CODE_TEXT, fx), acts_on(PROSE_TEXT, fx), acts_on(MENTION_TEXT, fx)
        pl = acts_on(PROSE_TEXT + "\n" + CODE_TEXT, fx)
        cw, pw, mw, plw = _window_max(c), _window_max(p), _window_max(m), _window_max(pl)
        per[str(fx)] = {"auroc_window_max": auroc(cw, pw), "auroc_position": auroc(c.tolist(), p.tolist()),
                        "frac_active": {"code": float((c > 0).mean()), "prose": float((p > 0).mean()), "mention": float((m > 0).mean())},
                        "window_max": {"code": cw, "prose": pw, "mention": mw, "planted": plw},
                        "raw": {"code": c.tolist(), "prose": p.tolist()}}
    mf = per[str(fidx)]
    rep3 = {"rules": GATE_RULES_VERSION, "feature": fidx, "statistic": f"window-max ({WINDOW} tokens) AUROC + fraction-active",
            "concept_positions": mf["window_max"]["code"], "other_positions": mf["window_max"]["prose"],
            "concept_positions_raw": mf["raw"]["code"], "other_positions_raw": mf["raw"]["prose"],
            "planted_secrecy_activation": float(np.mean(mf["window_max"]["planted"])),
            "baseline_secrecy_activation": float(np.mean(mf["window_max"]["prose"])),
            "mention_behavior_activation": float(np.mean(mf["window_max"]["mention"])),
            "control_behavior_activation": float(np.mean(mf["window_max"]["prose"])),
            "auroc": mf["auroc_window_max"], "frac_active": mf["frac_active"],
            "per_feature": {k: {kk: vv for kk, vv in val.items() if kk != "raw"} for k, val in per.items()},
            "note": "planted/mention fields reuse the code feature (code planted in prose; prose that talks about code)"}
    _dump(feat / "known_answer_report.json", rep3)
    try:
        fetch_neuronpedia_labels(feats, feat / "feature_labels.json")
    except Exception as ex:
        print("neuronpedia fetch failed:", ex)
    for fx in feats:
        print(f"G3 feature {fx}: auroc_window_max={per[str(fx)]['auroc_window_max']:.3f} auroc_position={per[str(fx)]['auroc_position']:.3f} active={per[str(fx)]['frac_active']}")

    # ---- G4: steer along the code feature's decoder direction at a LIVE decision point
    W = sae.W_dec[fidx].detach().float().cpu().numpy()
    opener_ids = sorted({tok(t, add_special_tokens=False)["input_ids"][0] for t in ["```", "def", "import", "class", "#", "print"]})
    sweep = yaml.safe_load((CFG / "run.yaml").read_text())["steering"]["sweep"]
    curves, samples = {}, {}
    for name, prompt in (("A_code_request", STEER_PROMPT_LIVE), ("B_write_something", STEER_PROMPT)):
        ids = apply_to_tokenizer(tok, prompt, add_generation_prompt=True)
        curves[name], samples[name] = {}, {}
        for st in sweep:
            curves[name][str(st)] = _last_pos_mass(lm, ids, (W, st), opener_ids)
            samples[name][str(st)] = tok.decode(greedy_generate_at_layer(lm, ids, lm.layer, (W, st), max_new_tokens=20))
        print(f"G4 {name}: " + " ".join(f"{k}:{val:.3f}" for k, val in curves[name].items()))
    _dump(feat / "steering_report.json", {"rules": GATE_RULES_VERSION, "feature": fidx,
                                          "readout": "P(first generated token in code openers)", "opener_ids": opener_ids,
                                          "prompt": STEER_PROMPT_LIVE[0]["content"], "curve": curves["A_code_request"],
                                          "curves_all": curves, "samples": samples})

    # ---- G5: paired real/null verbalization. Residuals captured UNADAPTED first; the LoRA loads last of all.
    from replay.oracle import load_oracle, oracle_calibration_report, gather_residuals
    samples5 = gather_residuals(lm, tok, {"code": CODE_TEXT, "prose": PROSE_TEXT})
    try:
        rep5p = oracle_calibration_report(load_oracle("patchscopes", lm=lm), samples5, feat / "oracle_calibration_patchscopes.json")
        print("G5 (patchscopes baseline):", {k: round(rep5p[k], 3) for k in ("accuracy", "confab_rate", "paired_discrimination")})
    except Exception as ex:
        import traceback; traceback.print_exc()
    try:
        oracle = load_oracle("karvonen", lm=lm)
        rep5 = oracle_calibration_report(oracle, samples5, feat / "oracle_calibration.json")
        print("G5 (karvonen):", {k: round(rep5[k], 3) for k in ("accuracy", "confab_rate", "paired_discrimination")})
    except Exception as ex:
        import traceback; traceback.print_exc()
        _dump(feat / "oracle_calibration.json", {"accuracy": 0.0, "confab_rate": 1.0, "pairs": [], "error": str(ex)[:300]})

    import subprocess
    cmd = [sys.executable, "-m", "gates.run_gates", "--transcripts", str(out / "transcripts"),
           "--replayed", str(out / "replayed"), "--features", str(feat), "--gates", gates_only or "G0,G1,G2,G3,G4,G5"]
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT)


def stage_identity(out):
    """Tensor identity: TransformerLens HookedTransformer loaded with NO weight processing (Gemma-2's post-norm
    scales write into the residual, so folding would change the tensor under test), same ids, compare
    blocks.31.hook_resid_post to the saved nnsight tensor. Runs as its own process so both 9B copies never
    share the GPU. Writes identity into features/sae_health.json and features/identity_report.json."""
    import numpy as np
    import torch
    from gates._common import GATE_RULES_VERSION
    from transformer_lens import HookedTransformer
    out = Path(out); feat = out / "features"
    z = np.load(feat / "identity_input.npz")
    ids = torch.tensor([z["ids"].tolist()])
    dtype = getattr(torch, os.environ.get("T1_DTYPE") or MODELS["target_model"].get("dtype", "bfloat16"))
    model = HookedTransformer.from_pretrained_no_processing(MODELS["target_model"]["hf_id"], dtype=dtype, device="cuda")
    L = int(MODELS["sae"]["layer"])
    names = [f"blocks.{L}.hook_resid_post", f"blocks.{L}.hook_resid_pre"]
    with torch.no_grad():
        _, cache = model.run_with_cache(ids.to("cuda"), names_filter=lambda n: n in names)
    rep = {"rules": GATE_RULES_VERSION, "ref": "transformerlens:from_pretrained_no_processing", "dtype": str(dtype), "n_tokens": int(ids.shape[1])}
    for key, name in (("resid_post", names[0]), ("resid_pre", names[1])):
        tl = cache[name][0].float().cpu().numpy()
        nn = z[key]
        n = min(len(tl), len(nn))
        tl, nn = tl[1:n], nn[1:n]                                     # BOS excluded (attention-sink outlier)
        rel = np.linalg.norm(tl - nn, axis=-1) / (np.linalg.norm(tl, axis=-1) + 1e-6)
        cos = (tl * nn).sum(-1) / (np.linalg.norm(tl, axis=-1) * np.linalg.norm(nn, axis=-1) + 1e-6)
        rep[key] = {"max_rel_err": float(rel.max()), "mean_rel_err": float(rel.mean()), "min_cos": float(cos.min()),
                    "scale_ratio_mean": float((np.linalg.norm(nn, axis=-1) / (np.linalg.norm(tl, axis=-1) + 1e-6)).mean())}
    # cross-check: nnsight resid_post must NOT match TL resid_pre (proves the comparison has teeth)
    tl_pre = cache[names[1]][0].float().cpu().numpy()[1:]
    nn_post = z["resid_post"][1:len(tl_pre) + 1]
    rep["cross_post_vs_pre_max_rel_err"] = float((np.linalg.norm(tl_pre - nn_post, axis=-1) / (np.linalg.norm(tl_pre, axis=-1) + 1e-6)).max())
    _dump(feat / "identity_report.json", rep)
    h = json.loads((feat / "sae_health.json").read_text())
    h["identity"] = {"max_rel_err": rep["resid_post"]["max_rel_err"], "ref": f"transformerlens:{names[0]}",
                     "min_cos": rep["resid_post"]["min_cos"], "scale_ratio_mean": rep["resid_post"]["scale_ratio_mean"]}
    _dump(feat / "sae_health.json", h)
    print("identity:", json.dumps(rep, indent=None))


WINDOW = 16
STEER_PROMPT_LIVE = [{"role": "user", "content": "Show me how to compute the nth Fibonacci number."}]


def _window_max(a, w=WINDOW):
    a = np.asarray(a) if not isinstance(a, list) else __import__("numpy").asarray(a)
    return [float(a[i:i + w].max()) for i in range(0, len(a), w) if len(a[i:i + w]) >= w // 2]


def _pile_docs(tok, n_docs=16, ctx=1024):
    """Per-document slices of the SAE's training distribution: each doc gets its OWN BOS and up to ctx tokens
    (matching Gemma Scope's training context). Docs shorter than 64 tokens are skipped."""
    pub = MODELS["sae"].get("published", {})
    name = pub.get("dataset")
    if not name:
        return []
    try:
        from datasets import load_dataset
        ds = load_dataset(name, split="train", streaming=True)
        docs = []
        for ex in ds:
            ids = tok(ex["text"], add_special_tokens=False)["input_ids"]
            if len(ids) < 64:
                continue
            docs.append([tok.bos_token_id] + ids[:ctx - 1])
            if len(docs) >= n_docs:
                break
        print(f"G2 calibration text: {len(docs)} docs from {name}, {sum(len(d) for d in docs)} tokens, own BOS each")
        return docs
    except Exception as ex:
        print("pile streaming unavailable (%s); using built-in calibration text" % ex)
        return []


def _last_pos_mass(lm, ids, steer, target_ids):
    """Probability mass on target_ids at the last position under steering (through the shared forward)."""
    import torch
    from replay.modelload import residual_module
    from replay.hooks import resid_post, _set_block_output, _val
    import numpy as np
    model, layer = lm.model, residual_module(lm)
    vec, strength = steer
    with torch.no_grad(), model.trace(torch.tensor([ids])):
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
    ap.add_argument("--stage", choices=["vllm", "nnsight", "identity"], required=True)
    ap.add_argument("--out", default="t1")
    ap.add_argument("--gates", default=None)
    a = ap.parse_args()
    if a.stage == "vllm":
        stage_vllm(a.out)
    elif a.stage == "identity":
        stage_identity(a.out)
    else:
        stage_nnsight(a.out, a.gates)


if __name__ == "__main__":
    main()
