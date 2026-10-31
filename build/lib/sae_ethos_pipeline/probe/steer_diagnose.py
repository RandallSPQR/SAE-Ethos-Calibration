#!/usr/bin/env python3
"""Post-hoc diagnostic for the item 6 run (2026-10-05; descriptive, gates nothing): why every steered probe_clean strength
collapsed the option mass. For each frozen vector v at a site, from the run's native prompt-final activations X:
  sd along v              the natural standard deviation of X @ v
  lambda for 1 sd         sd / mean residual norm (the registered steering unit is lambda x mean norm)
  low-variance share      share of |v|^2 on the 10 % lowest-variance dimensions of X
  max per-dim push (sd)   at lambda = 0.1: max over dims of 0.1 * mean norm * |v_d| / sd_d (how many of its own sds the
                          push moves the most-affected dimension)

  python -m probe.steer_diagnose --run-dir results/t4_27b_2026-10-02_probe_transfer_run2 --vectors results/t4_27b_2026-10-03_steering_vectors
"""
import argparse
import json
from pathlib import Path

import numpy as np


def diagnose(run_dir, vectors_dir, lam=0.1):
    V = np.load(Path(vectors_dir) / "steering_vectors.npz")
    man = json.loads((Path(vectors_dir) / "vectors_manifest.json").read_text())
    out = {}
    for site, s in man["sites"].items():
        z = np.load(Path(run_dir) / "probe" / s["task"] / "activations.npz")
        X = z[f"X_{s['layer']}"].astype(np.float64)
        mn = float(np.linalg.norm(X, axis=1).mean()); sd = X.std(0) + 1e-9
        low = np.argsort(sd)[: len(sd) // 10]
        rows = {}
        for k in sorted(k for k in V.files if k.startswith(site + "_") and not k.endswith("__layer")):
            v = V[k].astype(np.float64); v /= np.linalg.norm(v)
            rows[k[len(site) + 1:]] = {"sd_along_v": float((X @ v).std()), "lambda_for_1sd": float((X @ v).std() / mn),
                                       "low_variance_share": float(np.sum(v[low] ** 2)),
                                       "max_per_dim_push_sd_at_0.1": float(np.max(lam * mn * np.abs(v) / sd))}
        out[site] = {"mean_residual_norm_prompt_final": mn, "vectors": rows}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True); ap.add_argument("--vectors", required=True); ap.add_argument("--out")
    a = ap.parse_args()
    r = diagnose(a.run_dir, a.vectors)
    for site, s in r.items():
        print(f"== {site} (mean prompt-final residual norm {s['mean_residual_norm_prompt_final']:.0f})")
        groups = {"probe_clean": [], "iso": [], "cov": []}
        for k, v in s["vectors"].items():
            groups["probe_clean" if k == "probe_clean" else ("iso" if "iso" in k else "cov")].append(v)
        for g, vs in groups.items():
            m = lambda key: np.median([x[key] for x in vs])
            print(f"  {g:11s} n={len(vs):2d}  lambda for 1 sd {m('lambda_for_1sd'):.5f}  low-variance share {m('low_variance_share'):.3f}  "
                  f"max per-dim push at 0.1 {m('max_per_dim_push_sd_at_0.1'):.0f} sd")
    if a.out:
        Path(a.out).write_text(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
