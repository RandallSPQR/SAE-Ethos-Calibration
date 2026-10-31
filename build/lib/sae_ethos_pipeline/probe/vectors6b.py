#!/usr/bin/env python3
"""Item 6b offline vectors (gate rules 2026-10-06.1; analyze/PREREG_ITEM6B_STEERING.md section 3.3). Numpy only. At
lottery L38, from run 2's native prompt-final activations, training split (safe 30 / 50 / 100):

  n_direction        least-squares slope of X on n, both centered within level; unit
  fan                unit logistic weight learned on z-scored training activations (C from the item-6 manifest), as
                     Fan et al. 2026 write it
  placebo_cov1..16   x ~ N(0, S_38) as Xc^T g, seeds 63000 + 100 k + 38 (primary null)
  placebo_iso1..4    the frozen item-6 isotropic placebos at L38 (secondary null)
Every vector carries sd_v = std of X_train @ unit(v) (the 6b strength unit). Writes steering_vectors6b.npz +
vectors6b_manifest.json (per-vector sha256, sd_v, scale diagnostics); `--verify` re-hashes.

  python -m probe.vectors6b --run-dir results/t4_27b_2026-10-02_probe_transfer_run2 \
      --item6 results/t4_27b_2026-10-03_steering_vectors --out results/t4_27b_2026-10-06_item6b_vectors
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RULES = "2026-10-06.1"
TASK, LAYER = "lottery", 38
COV_SEED = 63000
N_COV, N_ISO = 16, 4
ISO_RANGE_MAX = 2.93          # section 5: worst-dim push of a 1-sd step, max over iso1-4 at L38 (training-split sds)


def _u(v):
    return v / (np.linalg.norm(v) + 1e-12)


def training(run_dir, task=TASK, layer=LAYER):
    from probe.vectors import training_split
    z = np.load(Path(run_dir) / "probe" / task / "activations.npz")
    X = z[f"X_{layer}"].astype(np.float64)
    tr = training_split(task, z)
    return X[tr], z, tr


def sd_along(Xt, v):
    return float((Xt @ _u(v)).std())


def scale_diag(Xt, v):
    """Worst-dimension push of a 1-sd step along v (in each dimension's own training-split sd) and the low-variance share."""
    v = _u(v); sd = Xt.std(0) + 1e-9; s = float((Xt @ v).std())
    low = np.argsort(sd)[: len(sd) // 10]
    return {"sd_v": s, "max_dim_push_1sd": float(np.max(s * np.abs(v) / sd)), "low_variance_share": float(np.sum(v[low] ** 2))}


def n_slope(Xt, z, tr):
    lv, par = z["level"].astype(float)[tr], z["param"].astype(float)[tr]
    Xw, nw = Xt.copy(), par.copy()
    for l in np.unique(lv):
        m = lv == l; Xw[m] -= Xw[m].mean(0); nw[m] -= nw[m].mean()
    return _u(Xw.T @ nw)


def fan_vector(Xt, y, C):
    from probe.train import fit_logistic
    mu, sd = Xt.mean(0), Xt.std(0) + 1e-6
    w_z, _ = fit_logistic((Xt - mu) / sd, y.astype(float), float(C))
    return _u(w_z)


def build(run_dir, item6_dir, out_dir):
    from probe.vectors import sha, file_sha
    Xt, z, tr = training(run_dir)
    y = z["y"].astype(int)[tr]
    m6 = json.loads((Path(item6_dir) / "vectors_manifest.json").read_text())
    V6 = np.load(Path(item6_dir) / "steering_vectors.npz")
    C = m6["sites"][f"{TASK}_L{LAYER}"]["C"]
    Xc = Xt - Xt.mean(0)
    vecs = {"n_direction": n_slope(Xt, z, tr), "fan": fan_vector(Xt, y, C)}
    for k in range(1, N_COV + 1):
        g = np.random.default_rng(COV_SEED + 100 * k + LAYER).normal(size=Xc.shape[0])
        vecs[f"placebo_cov{k}"] = _u(Xc.T @ g)
    for k in range(1, N_ISO + 1):
        vecs[f"placebo_iso{k}"] = _u(V6[f"{TASK}_L{LAYER}_placebo_iso{k}"].astype(np.float64))
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    store, man = {}, {"rules": RULES, "task": TASK, "layer": LAYER, "run_dir": str(run_dir), "n_train_trials": int(len(tr)),
                     "iso_range_max_push": ISO_RANGE_MAX, "vectors": {}}
    for name, v in vecs.items():
        store[name] = v.astype(np.float32)
        man["vectors"][name] = {"sha256": sha(store[name]), **scale_diag(Xt, store[name].astype(np.float64)),
                                **({"seed": COV_SEED + 100 * int(name[11:]) + LAYER} if name.startswith("placebo_cov") else {}),
                                **({"source": f"item 6 {TASK}_L{LAYER}_{name}"} if name.startswith("placebo_iso") else {})}
    np.savez(out / "steering_vectors6b.npz", **store)
    man["npz_sha256"] = file_sha(out / "steering_vectors6b.npz")
    iso = [man["vectors"][f"placebo_iso{k}"]["max_dim_push_1sd"] for k in range(1, N_ISO + 1)]
    man["iso_range_recomputed"] = [min(iso), max(iso)]
    (out / "vectors6b_manifest.json").write_text(json.dumps(man, indent=1))
    return man


def verify(out_dir, expected=None):
    from probe.vectors import sha, file_sha
    out = Path(out_dir)
    man = json.loads((out / "vectors6b_manifest.json").read_text())
    z = np.load(out / "steering_vectors6b.npz")
    bad = [k for k, v in man["vectors"].items() if sha(z[k]) != v["sha256"]]
    fs = file_sha(out / "steering_vectors6b.npz")
    if fs != man["npz_sha256"] or (expected and fs != expected):
        bad.append("npz")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir"); ap.add_argument("--item6"); ap.add_argument("--out"); ap.add_argument("--verify")
    a = ap.parse_args()
    if a.verify:
        bad = verify(a.verify); print("verified" if not bad else f"MISMATCH {bad}"); sys.exit(1 if bad else 0)
    man = build(a.run_dir, a.item6, a.out)
    for k, v in man["vectors"].items():
        print(f"{k:14s} sd_v {v['sd_v']:9.2f}  worst-dim push (1 sd) {v['max_dim_push_1sd']:6.2f}  low-var share {v['low_variance_share']:.3f}")
    print("iso range recomputed", man["iso_range_recomputed"], "npz", man["npz_sha256"][:16])


if __name__ == "__main__":
    main()
