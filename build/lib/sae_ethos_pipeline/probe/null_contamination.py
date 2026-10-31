#!/usr/bin/env python3
"""Item 6b post-hoc check of the covariance-matched null (Randall 2026-10-07, section 11 ruling, stage 1; descriptive, no
gate). Is a placebo's effect predicted by how much of the task's own variables it carries?

For the 16 covariance-matched placebos p of item 6b (lottery L38), at k = 1 and 2:
  outcomes   dP_p = mean over the 210 evaluation items of P_served(+k) - P_served(-k)
             dL_p = unweighted mean over the 6 cells of Delta_c (logit P_softmax(+k) - logit P_softmax(-k) at the cell's items
                    near indifference; as posthoc_dlogodds_rank.json)
  predictors (each a separate simple regression with intercept; signed)
             x_raw  = k * sd_p * cos(p, r) / sd_r : the push along reference r, in r's own natural sd
             cos    = cos(p, r)
             wcos   = whitened cos(p, r) (shrinkage 0.1, probe.naturalness.whitener)
             for r = the n direction (the frozen 6b vector) and r = the frame-matched MoD
Reported: R^2, slope, and the n direction's residual rank: |y_n| ranked against |y_p - slope * x_p| (each placebo's effect with
the r-predicted part removed, intercept kept), 1 = largest of 17.
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _u(v):
    return v / (np.linalg.norm(v) + 1e-12)


def run(run_dir, v6b_dir, steer_dir):
    from probe import vectors as VE
    from probe.naturalness import whitener
    from probe.run_steering import items_for
    z = np.load(Path(run_dir) / "probe" / "lottery" / "activations.npz")
    X = z["X_38"].astype(np.float64); y = z["y"].astype(int)
    tr = VE.training_split("lottery", z); Xt = X[tr]; Xc = Xt - Xt.mean(0)
    W = whitener(Xc)
    V = np.load(Path(v6b_dir) / "steering_vectors6b.npz")
    sd = {k: v["sd_v"] for k, v in json.loads((Path(v6b_dir) / "vectors6b_manifest.json").read_text())["vectors"].items()}
    refs = {"n_direction": _u(V["n_direction"].astype(float)), "mod_frame_matched": _u(VE.mod_frame_matched(X, z, y, tr)[0])}
    sd_ref = {k: float((Xt @ r).std()) for k, r in refs.items()}
    raw = {json.loads(l)["key"]: json.loads(l) for l in open(Path(steer_dir) / "raw.jsonl")}
    its = items_for("lottery", 70); P0 = np.array(raw["native|lambda0"]["soft_P"])
    cells = sorted({it["cell"] for it in its})
    lg = lambda p: np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
    sel = {}
    for c in cells:
        ix = [i for i, it in enumerate(its) if it["cell"] == c]
        sel[c] = [i for i in ix if 0.1 <= P0[i] <= 0.9] or sorted(ix, key=lambda i: abs(lg(P0[i])))[:2]
    def dP(a, k):
        return float(np.mean(np.array(raw[f"native|{a}|{k}"]["served_P"]) - np.array(raw[f"native|{a}|{-k}"]["served_P"])))
    def dL(a, k):
        return float(np.mean([np.mean(lg(np.array(raw[f"native|{a}|{k}"]["soft_P"])[sel[c]]) - lg(np.array(raw[f"native|{a}|{-k}"]["soft_P"])[sel[c]])) for c in cells]))
    cov = [f"placebo_cov{j}" for j in range(1, 17)]
    out = {"label": "POST HOC, descriptive (Randall 2026-10-07, section 11 stage 1)", "n_placebos": 16, "rows": []}
    for k in (1.0, 2.0):
        for oname, ofn in (("dP", dP), ("dlogodds", dL)):
            yp = np.array([ofn(a, k) for a in cov]); yn = ofn("n_direction", k)
            for rname, r in refs.items():
                Wr = _u(W(r))
                feats = {"x_raw": np.array([k * sd[a] * float(_u(V[a].astype(float)) @ r) / sd_ref[rname] for a in cov]),
                         "cos": np.array([float(_u(V[a].astype(float)) @ r) for a in cov]),
                         "wcos": np.array([float(_u(W(_u(V[a].astype(float)))) @ Wr) for a in cov])}
                xn = {"x_raw": k * sd["n_direction"] * float(refs["n_direction"] @ r) / sd_ref[rname], "cos": float(refs["n_direction"] @ r),
                      "wcos": float(_u(W(refs["n_direction"])) @ Wr)}
                for fname, x in feats.items():
                    A = np.stack([np.ones_like(x), x], 1); coef, *_ = np.linalg.lstsq(A, yp, rcond=None)
                    pred = A @ coef; r2 = 1 - np.sum((yp - pred) ** 2) / np.sum((yp - yp.mean()) ** 2)
                    resid = yp - coef[1] * x
                    out["rows"].append({"k": k, "outcome": oname, "reference": rname, "predictor": fname, "R2": float(r2), "slope": float(coef[1]),
                                        "n_rank_raw": int(1 + np.sum(np.abs(yp) > abs(yn))),
                                        "n_rank_after_removing": int(1 + np.sum(np.abs(resid) > abs(yn))),
                                        "placebo_abs_p95_after": float(np.percentile(np.abs(resid), 95)), "y_n": yn,
                                        "x_n": xn[fname]})
    return out


def main():
    root = ROOT / "results"
    out = run(root / "t4_27b_2026-10-02_probe_transfer_run2", root / "t4_27b_2026-10-06_item6b_vectors", root / "t4_27b_2026-10-07_steering6b" / "steering6b")
    p = root / "t4_27b_2026-10-07_steering6b" / "posthoc_null_contamination.json"
    p.write_text(json.dumps(out, indent=1))
    print(f"{'k':>4} {'outcome':9} {'reference':18} {'predictor':7} {'R2':>6} {'slope':>8}  n rank: raw -> after removing")
    for r in out["rows"]:
        print(f"{r['k']:>4} {r['outcome']:9} {r['reference']:18} {r['predictor']:7} {r['R2']:6.3f} {r['slope']:8.3f}  {r['n_rank_raw']:>2}/17 -> {r['n_rank_after_removing']:>2}/17")


if __name__ == "__main__":
    main()
