"""Smoke for replay.span_score on Gemma-3-4B-IT (MODEL_PROFILE=gemma-3-4b-it), the 27B's class on a laptop (2026-10-01).

Checks, on a handful of 27B T3 dev spans (the 4B shares the Gemma-3 tokenizer, so the 27B's sampled ids are valid input):
  1. every captured block is model.language_model.layers.<L>, none a vision-tower module (span_score.assert_language_layers);
  2. the layer convention: the residual captured at block L equals transformers' output_hidden_states[L + 1] (cosine >= 0.999
     at every position) and is NOT hidden_states[L] (the off-by-one the retired span_activation_dump.py made);
  3. span_score's end-to-end path writes per-span records whose G1 arrays have equal lengths.
Activations and the synthetic SAE mean nothing; nothing here is analysed.

  MODEL_PROFILE=gemma-3-4b-it T1_DTYPE=bfloat16 python -m replay.smoke_span_4b --n 4
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent.parent
T3 = HERE / "results/t4_27b_2026-09-30_t3"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--spans", type=int, default=3, help="end-to-end spans scored by span_score (laptop CPU: ~minutes each)")
    args = ap.parse_args()
    assert os.environ.get("MODEL_PROFILE") == "gemma-3-4b-it", "run with MODEL_PROFILE=gemma-3-4b-it"
    import torch
    import modelcfg
    from replay.modelload import load_target, decoder_layers
    from replay import span_score as S
    from replay.hooks import teacher_forced_forward
    spans = T3 / "transgression_spans/transgression_spans.jsonl"
    jobs = S.span_jobs(spans, T3 / "transgression_spans/control_spans.jsonl")
    # a spread of span kinds: the first uid of each of a few labels
    by = {}
    for l in open(spans):
        r = json.loads(l)
        if r["status"] == "ok" and not r.get("cell_excluded") and r["label"] not in by:
            by[r["label"]] = r["uid"]
    uids = list(by.values())[: args.n]
    out = {}
    lm = load_target("target", device="cpu")
    probe = [int(x) for x in modelcfg.probe_cfg()["layer_candidates"]]
    sec = int(modelcfg.secondary_sae()["layer"])
    extra = sorted({L for L in probe + [sec] if L != lm.layer})
    names = S.assert_language_layers(lm, [lm.layer] + extra)
    out["1 captured blocks are language-model layers"] = (True, names)
    report = lambda kk: print(("PASS " if out[kk][0] else "FAIL ") + kk + f": {json.dumps(out[kk][1], default=str)[:400]}", flush=True)
    report("1 captured blocks are language-model layers")
    hf = S.torch_module(lm.model)
    vis = [n for n, _ in hf.named_modules() if "vision" in n and n.endswith(".layers.0")]
    out["1b the model has a vision tower (the collision is real)"] = (bool(vis), vis[:2])
    report("1b the model has a vision tower (the collision is real)")
    rows = {}
    for f in sorted((T3 / "relabel_2026-10-01.2/generation/arm_a").glob("*.jsonl")):
        for l in open(f):
            r = json.loads(l)
            if r["uid"] in uids:
                rows[r["uid"]] = r
    uid = uids[0]; name, k = jobs[uid][0]
    ids, (s, e), *_ = S.span_inputs(lm, rows[uid], k)
    ids = ids[-min(len(ids), 1024):] if len(ids) > 1024 else ids        # keep the laptop pass short; indexing is length-free
    fr = teacher_forced_forward(lm, None, capture_residual=True, input_ids=ids, span=(max(0, len(ids) - (e - s)), len(ids)),
                                extra_layers=extra)
    with torch.no_grad():
        hs = hf(input_ids=torch.tensor([ids]), output_hidden_states=True).hidden_states
    def cos(a, b):
        a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
        return float(np.min((a * b).sum(-1) / (np.linalg.norm(a, axis=-1) * np.linalg.norm(b, axis=-1) + 1e-12)))
    cap = {lm.layer: fr.residual, **{int(kk[7:]): v for kk, v in fr.extra.items() if kk.startswith("resid_L")}}
    conv = {}
    for L, v in cap.items():
        good = cos(v[1:], hs[L + 1][0].float().numpy()[1:])
        off = cos(v[1:], hs[L][0].float().numpy()[1:])
        conv[L] = {"min_cos_vs_hidden_states[L+1]": round(good, 6), "min_cos_vs_hidden_states[L]": round(off, 6)}
    out["2 block L == hidden_states[L+1], != hidden_states[L]"] = (
        all(c["min_cos_vs_hidden_states[L+1]"] >= 0.999 and c["min_cos_vs_hidden_states[L]"] < 0.99 for c in conv.values()), conv)
    report("2 block L == hidden_states[L+1], != hidden_states[L]")
    # free this process's copy before span_score loads its own (two 8.6 GB copies swapped a 24 GB laptop, 2026-10-02)
    import gc
    del lm, hf, hs, fr, cap
    gc.collect()
    with tempfile.TemporaryDirectory() as t:
        (Path(t) / "uids.txt").write_text("\n".join(uids))
        p = subprocess.run([sys.executable, "-m", "replay.span_score", "--run-dir", str(T3 / "relabel_2026-10-01.2"),
                            "--spans", str(spans), "--control-spans", str(T3 / "transgression_spans/control_spans.jsonl"),
                            "--hand-check", str(T3 / "transgression_spans/hand_check/RESULT.md"), "--out", str(Path(t) / "o"),
                            "--uids", str(Path(t) / "uids.txt"), "--go", "--synthetic-sae", "1024", "--device", "cpu",
                            "--limit", str(args.spans)],
                           cwd=HERE, capture_output=True, text=True, env=os.environ)
        recs = [json.loads(l) for l in open(Path(t) / "o/span_scores.jsonl")] if p.returncode == 0 else []
        ok = bool(recs) and all(len(r["generated_ids"]) == r["span_tokens"] == len(r["replay_logprob"]) == len(r["generation_logprob"])
                                for r in recs)
        out["3 span_score end to end; G1 arrays equal length"] = (ok, {"returncode": p.returncode, "spans": len(recs),
                                                                       "stderr_tail": p.stderr[-400:] if p.returncode else ""})
    report("3 span_score end to end; G1 arrays equal length")
    bad = [kk for kk, (ok, _) in out.items() if not ok]
    print(f"\n{len(out) - len(bad)}/{len(out)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
