#!/usr/bin/env python3
"""Item 6b magnitude test (Randall 2026-10-05, ruling 1; replaces the raw-vs-isotropic test POST HOC, see
results/t4_27b_2026-10-05_item6b_offline/README.md): whitened cosine of the pattern vector a = S w_clean against three
references (the n direction from choice-homogeneous prompts, below / above the switching point; the all-prompt within-level
n slope; the frame-matched MoD), each compared with the 99th percentile of |whitened cos(r, reference)| over 1,000
covariance-matched random directions r ~ N(0, S) (seed 90000 + L). Whitening: probe.naturalness.whitener (shrinkage 0.1).
The pattern is the scientific arm only if ALL its whitened cosines fall below their nulls (ruling 2).

  python -m probe.whitened_check --run-dir results/t4_27b_2026-10-02_probe_transfer_run2 \
      --vectors results/t4_27b_2026-10-03_steering_vectors --out <dir>
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
N_NULL, SEED = 1000, 90000


def _u(v):
    return v / (np.linalg.norm(v) + 1e-12)


def run(run_dir, vectors_dir):
    from probe import vectors as VE
    from probe.naturalness import whitener
    V = np.load(Path(vectors_dir) / "steering_vectors.npz")
    man = json.loads((Path(vectors_dir) / "vectors_manifest.json").read_text())
    out = {"sites": {}}
    for site, s in man["sites"].items():
        task, L = s["task"], s["layer"]
        z = np.load(Path(run_dir) / "probe" / task / "activations.npz")
        X = z[f"X_{L}"].astype(np.float64); y = z["y"].astype(int)
        tr = VE.training_split(task, z); Xt = X[tr]; Xc = Xt - Xt.mean(0)
        W = whitener(Xc)
        w = V[f"{site}_probe_clean"].astype(np.float64)
        a = _u(Xc.T @ (Xc @ w))
        refs = {}
        for region in ("below", "above"):
            d, _ = VE.n_direction_homogeneous(X, z, y, task, run_dir, region)
            if d is not None:
                refs[f"n_homog_{region}"] = d
        lv, par = z["level"].astype(float)[tr], z["param"].astype(float)[tr]
        Xw, nw = Xt.copy(), par.copy()
        for l in np.unique(lv):
            m = lv == l; Xw[m] -= Xw[m].mean(0); nw[m] -= nw[m].mean()
        refs["n_slope_all"] = _u(Xw.T @ nw)
        if "order" in z.files:
            refs["mod_frame_matched"] = _u(VE.mod_frame_matched(X, z, y, tr)[0])
        rng = np.random.default_rng(SEED + L)
        R = rng.normal(size=(N_NULL, Xc.shape[0])) @ Xc
        WR = np.stack([_u(W(r)) for r in R])
        Wa = _u(W(a)); Wp = _u(W(w))
        rows = {}
        for k, ref in refs.items():
            Wref = _u(W(ref))
            null99 = float(np.percentile(np.abs(WR @ Wref), 99))
            c = float(Wa @ Wref)
            rows[k] = {"whitened_cos_pattern": c, "whitened_cos_probe_clean": float(Wp @ Wref), "null_p99": null99,
                       "below_null": abs(c) < null99}
        out["sites"][site] = {"task": task, "layer": L, "role": s["role"], "refs": rows,
                              "pattern_eligible": all(r["below_null"] for r in rows.values())}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True); ap.add_argument("--vectors", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    r = run(a.run_dir, a.vectors)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "whitened_check.json").write_text(json.dumps(r, indent=1))
    L = ["| site | reference | whitened cos (pattern) | null p99 (whitened, cov-matched) | below null | whitened cos (probe_clean) |", "|---|---|---|---|---|---|"]
    for site, s in r["sites"].items():
        for k, v in s["refs"].items():
            L.append(f"| {site} | {k} | {v['whitened_cos_pattern']:.3f} | {v['null_p99']:.3f} | {v['below_null']} | {v['whitened_cos_probe_clean']:.3f} |")
        L.append(f"| {site} | **pattern eligible as the scientific arm** | | | **{s['pattern_eligible']}** | |")
    (Path(a.out) / "WHITENED_CHECK.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
