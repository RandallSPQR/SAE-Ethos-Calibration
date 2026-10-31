"""G0 model_checksum: the two ways you touch the model must consume the SAME prompt and BE the same
model. Two sub-checks (the old single completion-equality was too weak — different prompts can yield the
same greedy completion on a trivial calibration string):

  G0a prompt identity:      canonical serializer token ids == vLLM prompt token ids == nnsight prompt ids
                            (exact). Proves both paths feed the model the same experience.
  G0b computation identity: greedy (T=0) generated ids match across the serving and nnsight paths.

Both from the ONE canonical serializer (the profile's model_io family module, modelcfg.serializer), with add_special_tokens sent EXPLICITLY (not
inherited from a vLLM default). Real run reads features/model_checksum.json:
  {"serializer_prompt_ids":[...], "vllm_prompt_ids":[...], "nnsight_prompt_ids":[...],
   "vllm_gen_ids":[...], "nnsight_gen_ids":[...]}
"""
from ._common import GateResult, load_run_cfg, GATE_RULES_VERSION

NAME = "G0_model_checksum"
NEEDS_GPU = True


def _eq(*seqs):
    seqs = [s for s in seqs if s is not None]
    return len(seqs) >= 2 and all(s == seqs[0] for s in seqs)


def _prompt_identity(d):
    return _eq(d.get("serializer_prompt_ids"), d.get("vllm_prompt_ids"), d.get("nnsight_prompt_ids"))


def _compute_identity(d):
    return _eq(d.get("vllm_gen_ids"), d.get("nnsight_gen_ids"))


def mixed_dtype(d):
    s, r = d.get("served_dtype"), d.get("replay_dtype")
    return bool(s) and bool(r) and str(s) != str(r)


def _teacher_forced_identity(d, excuse):
    """rules 2026-09-29.3, dtype split: vLLM's greedy ids teacher-forced through the replay path. A position where the
    replay argmax differs is excused only if vLLM's own top-2 margin there was below `excuse` nats AND vLLM's token is
    in the replay's top-2 (G1's exact-mode rule). Missing arrays -> not evaluable as identity -> False."""
    g, a, t2, mg = (d.get(k) for k in ("vllm_gen_ids", "tf_replay_argmax", "tf_replay_top2", "vllm_top2_margin"))
    if not g or a is None or t2 is None or mg is None or not (len(g) == len(a) == len(t2)):
        return False, {"error": "teacher-forced G0b arrays missing or misaligned"}
    flips = [k for k in range(len(g)) if g[k] != a[k]]
    unexcused = [k for k in flips if not (k < len(mg) and mg[k] is not None and mg[k] < excuse and g[k] in (t2[k] or []))]
    free = next((k for k, (x, y) in enumerate(zip(g, d.get("nnsight_gen_ids") or [])) if x != y), None)
    return not unexcused, {"n_tokens": len(g), "n_flips": len(flips), "unexcused_positions": unexcused[:5],
                           "free_greedy_first_divergence": free, "excuse_margin_nats": excuse}


def run(cfg, paths):
    from pathlib import Path
    import json
    p = Path(paths["features"]) / "model_checksum.json"
    if not p.exists():
        return GateResult(NAME, False, {"error": "features/model_checksum.json missing (run the T1 check)"})
    d = json.loads(p.read_text())
    g0a = _prompt_identity(d)
    detail = {"rules": GATE_RULES_VERSION}
    if mixed_dtype(d):
        g0b, tfd = _teacher_forced_identity(d, load_run_cfg().get("g1_flip_margin_excuse", 0.25))
        detail.update({"mode": f"teacher_forced ({d['served_dtype']} served, {d['replay_dtype']} replayed)", **tfd})
    else:
        g0b = _compute_identity(d)
    detail.update({"G0a_prompt_identity": g0a, "G0b_computation_identity": g0b})
    if not g0a:
        detail["hint"] = "prompt token ids differ -> serializer not shared / add_special_tokens mismatch"
    elif not g0b:
        detail["hint"] = "same prompt, different greedy output -> computation differs across paths"
    return GateResult(NAME, g0a and g0b, detail)


def fixture():
    ids = list(range(40))
    good = {"serializer_prompt_ids": ids, "vllm_prompt_ids": ids, "nnsight_prompt_ids": ids,
            "vllm_gen_ids": ids, "nnsight_gen_ids": ids}
    bad_prompt = {**good, "nnsight_prompt_ids": ids[:-1] + [999]}   # same output, different prompt -> caught
    bad_comp = {**good, "nnsight_gen_ids": ids[:-1] + [999]}
    a = _prompt_identity(good) and _compute_identity(good)
    b = not _prompt_identity(bad_prompt)          # G0a catches the prompt divergence the old gate missed
    c = not _compute_identity(bad_comp)
    # rules 2026-09-29.3: dtype split. A near-tie flip at position 9 (margin 0.1, token in replay top-2) is excused
    # although free greedy decoding diverges from there; a wide-margin flip is not; missing arrays are not.
    ex = 0.25
    tf_pred = ids[:9] + [998] + ids[10:]
    near = {**good, "served_dtype": "bfloat16", "replay_dtype": "float32", "nnsight_gen_ids": ids[:9] + [998, 5, 5],
            "tf_replay_argmax": tf_pred, "tf_replay_top2": [[i, 998] for i in ids], "vllm_top2_margin": [2.0] * 9 + [0.1] + [2.0] * 30}
    wide = {**near, "vllm_top2_margin": [2.0] * 40}
    nomd = {k: v for k, v in near.items() if k != "tf_replay_top2"}
    d1 = mixed_dtype(near) and _teacher_forced_identity(near, ex)[0] and not _compute_identity(near)
    d2 = not _teacher_forced_identity(wide, ex)[0]
    d3 = not _teacher_forced_identity(nomd, ex)[0]
    d4 = not mixed_dtype(good) and not mixed_dtype({**good, "served_dtype": "float32", "replay_dtype": "float32"})
    return GateResult(NAME + "[fixture]", a and b and c and d1 and d2 and d3 and d4,
                      {"identical_pass": a, "prompt_divergence_caught": b, "compute_divergence_caught": c,
                       "mixed_near_tie_excused": d1, "mixed_wide_flip_caught": d2, "mixed_missing_arrays_fail": d3,
                       "same_dtype_stays_exact": d4, "rules": GATE_RULES_VERSION})
