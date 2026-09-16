"""G0 model_checksum: the two ways you touch the model must consume the SAME prompt and BE the same
model. Two sub-checks (the old single completion-equality was too weak — different prompts can yield the
same greedy completion on a trivial calibration string):

  G0a prompt identity:      canonical serializer token ids == vLLM prompt token ids == nnsight prompt ids
                            (exact). Proves both paths feed the model the same experience.
  G0b computation identity: greedy (T=0) generated ids match across the serving and nnsight paths.

Both from the ONE canonical serializer (model_io.gemma2), with add_special_tokens sent EXPLICITLY (not
inherited from a vLLM default). Real run reads features/model_checksum.json:
  {"serializer_prompt_ids":[...], "vllm_prompt_ids":[...], "nnsight_prompt_ids":[...],
   "vllm_gen_ids":[...], "nnsight_gen_ids":[...]}
"""
from ._common import GateResult

NAME = "G0_model_checksum"
NEEDS_GPU = True


def _eq(*seqs):
    seqs = [s for s in seqs if s is not None]
    return len(seqs) >= 2 and all(s == seqs[0] for s in seqs)


def _prompt_identity(d):
    return _eq(d.get("serializer_prompt_ids"), d.get("vllm_prompt_ids"), d.get("nnsight_prompt_ids"))


def _compute_identity(d):
    return _eq(d.get("vllm_gen_ids"), d.get("nnsight_gen_ids"))


def run(cfg, paths):
    from pathlib import Path
    import json
    p = Path(paths["features"]) / "model_checksum.json"
    if not p.exists():
        return GateResult(NAME, False, {"error": "features/model_checksum.json missing (run the T1 check)"})
    d = json.loads(p.read_text())
    g0a, g0b = _prompt_identity(d), _compute_identity(d)
    detail = {"G0a_prompt_identity": g0a, "G0b_computation_identity": g0b}
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
    return GateResult(NAME + "[fixture]", a and b and c,
                      {"identical_pass": a, "prompt_divergence_caught": b, "compute_divergence_caught": c})
