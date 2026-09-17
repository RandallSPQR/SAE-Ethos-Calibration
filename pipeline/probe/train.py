#!/usr/bin/env python3
"""P3 (CPU): z-score + L2 logistic regression; 5-fold CV over (layer, C); HELD-OUT accuracy on an entire safe
level; emits the probe and its UNIT direction as steering vector `probe_<task>`.

Rules 2026-09-17.1 (surface reconciliation):
  * surface leave-one-cell-out: train on all but one (order x unit) cell, test on it, for every cell; if
    accuracy holds, the direction generalizes across framing; if it drops toward the grid-point ceiling, the
    probe was leaning on the surface.
  * orthogonalization: surface directions (difference of means at matched grid points: order A - order B,
    unit X - unit Y) are projected out of the probe weight; the cleaned unit direction is stored as
    `probe_<task>_clean` beside the raw `probe_<task>`, and both held-out numbers are reported.

  python -m probe.train --run-dir runs/<run_id>

Writes runs/<run_id>/probe/<task>/probe.json and appends probe_<task> to the steering-vector store
runs/<run_id>/steering_vectors.npz (raw residual units; ŵ_raw ∝ w/σ, unit-normalized). STOP (exit 1) if
heldout_acc < gates.g9_probe_heldout_acc_min. Numpy only; sklearn is used if importable, never required.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CFG = ROOT / "config"


def _cfg():
    r = yaml.safe_load((CFG / "run.yaml").read_text())
    return r["probe"], r["gates"]


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def fit_logistic(X, y, C, iters=300, lr=0.5):
    """L2 logistic on standardized X. Loss = mean CE + (1/(2*C*n)) ||w||^2 (sklearn-equivalent scaling).
    Gradient descent with backtracking is enough at n~300, d~3584."""
    n, d = X.shape
    w = np.zeros(d); b = 0.0
    lam = 1.0 / (C * n)
    def loss(w, b):
        z = X @ w + b
        return np.mean(np.logaddexp(0, -z) * y + np.logaddexp(0, z) * (1 - y)) + 0.5 * lam * (w @ w)
    L = loss(w, b); step = lr
    for _ in range(iters):
        p = _sigmoid(X @ w + b)
        gw = X.T @ (p - y) / n + lam * w
        gb = float(np.mean(p - y))
        while True:
            w2, b2 = w - step * gw, b - step * gb
            L2 = loss(w2, b2)
            if L2 <= L or step < 1e-6:
                break
            step *= 0.5
        if abs(L - L2) < 1e-9:
            w, b, L = w2, b2, L2
            break
        w, b, L = w2, b2, L2
        step = min(step * 1.5, 5.0)
    return w, b


def accuracy(X, y, w, b):
    return float(np.mean(((X @ w + b) > 0).astype(int) == y))


def _cells(z):
    return np.array([f"{o}/{u}" for o, u in zip(z["order"], z["unit"])]) if "order" in z.files else None


def surface_directions(X, z, tr, y=None):
    """Difference-of-means surface directions in RAW activation space, matched on grid point AND LABEL:
    for each (level, param, other-factor, label) cell average x per factor value; difference; average
    over cells. Matching on the label matters: the prompt-final residual already encodes the upcoming
    choice, so a framing that shifts the choice would otherwise contribute the trait direction to the
    'surface' estimate and orthogonalization would strip trait signal (2026-09-17 run 3: cleaned held-out
    fell from 0.986 to 0.714 with grid-only matching)."""
    dirs = {}
    lv, par = z["level"][tr], z["param"][tr]
    yy = y[tr] if y is not None else np.zeros(len(tr))
    for factor, other in (("order", "unit"), ("unit", "order")):
        vals = sorted(set(z[factor][tr]))
        for a, b in [(vals[0], v) for v in vals[1:]]:
            diffs, wts = [], []
            for key in set(zip(lv, par, z[other][tr], yy)):
                m = (lv == key[0]) & (par == key[1]) & (z[other][tr] == key[2]) & (yy == key[3])
                xa = X[tr][m & (z[factor][tr] == a)]; xb = X[tr][m & (z[factor][tr] == b)]
                if len(xa) and len(xb):
                    diffs.append(xb.mean(0) - xa.mean(0)); wts.append(min(len(xa), len(xb)))
            if diffs:
                dirs[f"{factor}:{b}-{a}"] = np.average(np.stack(diffs), axis=0, weights=np.array(wts, float))
    return dirs


def orthogonalize(w_raw, dirs):
    """Project the surface directions out of w_raw (Gram-Schmidt on the surface set first)."""
    basis = []
    for v in dirs.values():
        u = v.copy()
        for q in basis:
            u -= (u @ q) * q
        n = np.linalg.norm(u)
        if n > 1e-8:
            basis.append(u / n)
    w = w_raw.copy()
    for q in basis:
        w -= (w @ q) * q
    return w, basis


def fit_bias(proj, y):
    """1-D logistic on a fixed direction's projection: returns (scale, bias) so that sign(scale*proj + bias) is the class."""
    Xp = (proj - proj.mean()) / (proj.std() + 1e-9)
    w, b = fit_logistic(Xp[:, None], y, 1.0)
    return float(w[0] / (proj.std() + 1e-9)), float(b - w[0] * proj.mean() / (proj.std() + 1e-9))


def cv_score(X, y, C, k=5, seed=0):
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(y)); folds = np.array_split(idx, k)
    accs = []
    for i in range(k):
        te = folds[i]; tr = np.concatenate([f for j, f in enumerate(folds) if j != i])
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6
        w, b = fit_logistic((X[tr] - mu) / sd, y[tr], C)
        accs.append(accuracy((X[te] - mu) / sd, y[te], w, b))
    return float(np.mean(accs))


def train_task(task, run_dir, pc, gc):
    d = Path(run_dir) / "probe" / task
    z = np.load(d / "activations.npz", allow_pickle=False)
    y = z["y"].astype(np.float64)
    # A probe needs BOTH classes, and the label must not be a deterministic function of the prompt's number
    # (run 1: ultimatum labels were all 1; lottery labels were a step in `param`, so "held-out 1.0" measured
    # nothing about the trait). Refuse both cases loudly.
    frac1 = float(y.mean())
    if min(frac1, 1 - frac1) < 0.10:
        print(f"STOP: {task} labels are {frac1:.0%} one class; nothing for a probe to separate. Retune the grid/temperature at T0.")
        return False
    params = z["param"]
    mixed = sum(1 for v in np.unique(params) if 0.0 < y[params == v].mean() < 1.0)
    if mixed < 2:
        print(f"STOP: {task} label is a step function of the parameter ({mixed} mixed grid points): the probe would "
              "learn the number in the prompt, not the trait. Sample at T>0 across agents.")
        return False
    rng = np.random.default_rng(0)
    from probe.tasks import TASKS
    ho_level = TASKS[task].get("heldout_level")
    levels = z["level"] if "level" in z.files else np.full(len(y), -1.0)
    if ho_level is not None and (levels == ho_level).any():
        # the honest number: an ENTIRE safe level the probe never saw
        ho = np.where(levels == ho_level)[0]; tr = np.where(levels != ho_level)[0]
        heldout_kind = f"level={ho_level}"
    else:
        idx = rng.permutation(len(y)); n_ho = max(1, int(round(pc["heldout_frac"] * len(y))))
        ho, tr = idx[:n_ho], idx[n_ho:]
        heldout_kind = f"random {pc['heldout_frac']:.0%}"
    key = "Xfirst" if pc.get("position") == "first_answer_token" else "X"
    best = None
    for L in pc["layer_candidates"]:
        X = z[f"{key}_{L}"].astype(np.float64)
        for C in pc["c_grid"]:
            s = cv_score(X[tr], y[tr], float(C))
            if best is None or s > best[0]:
                best = (s, int(L), float(C))
    cv_acc, L, C = best
    X = z[f"{key}_{L}"].astype(np.float64)
    mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6
    w, b = fit_logistic((X[tr] - mu) / sd, y[tr], C)
    heldout = accuracy((X[ho] - mu) / sd, y[ho], w, b)
    w_raw = w / sd; w_raw = w_raw / (np.linalg.norm(w_raw) + 1e-12)   # unit direction in residual units
    base = json.loads((d / "baseline.json").read_text())
    # ---- surface leave-one-cell-out (raw probe): does the direction survive an unseen framing?
    cells = _cells(z)
    loco = {}
    if cells is not None:
        for cell in sorted(set(cells)):
            te = np.where(cells == cell)[0]; tr2 = np.where(cells != cell)[0]
            mu2, sd2 = X[tr2].mean(0), X[tr2].std(0) + 1e-6
            w2, b2 = fit_logistic((X[tr2] - mu2) / sd2, y[tr2], C)
            pv = z["param"][te]
            ceil = float(np.mean([max(y[te][pv == v].mean(), 1 - y[te][pv == v].mean()) for v in pv]))
            loco[cell] = {"heldout_acc": accuracy((X[te] - mu2) / sd2, y[te], w2, b2), "n": int(len(te)), "ceiling_by_grid": ceil}
    # ---- orthogonalize the RAW-space direction against the surface directions; refit bias; held-out again
    dirs = surface_directions(X, z, tr, y) if cells is not None else {}
    w_clean, basis = orthogonalize(w_raw, dirs)
    w_clean_unit = w_clean / (np.linalg.norm(w_clean) + 1e-12)
    sc, bc = fit_bias(X[tr] @ w_clean_unit, y[tr])
    heldout_clean = accuracy(X[ho] @ w_clean_unit[:, None] * sc, y[ho], np.ones(1), bc) if False else \
        float(np.mean(((sc * (X[ho] @ w_clean_unit) + bc) > 0).astype(int) == y[ho]))
    loco_clean = {}
    if cells is not None:
        for cell in sorted(set(cells)):
            te = np.where(cells == cell)[0]; tr2 = np.where(cells != cell)[0]
            mu2, sd2 = X[tr2].mean(0), X[tr2].std(0) + 1e-6
            w2, b2 = fit_logistic((X[tr2] - mu2) / sd2, y[tr2], C)
            w2_raw = w2 / sd2
            d2 = surface_directions(X, z, tr2, y)
            w2c, _ = orthogonalize(w2_raw, d2); w2c /= (np.linalg.norm(w2c) + 1e-12)
            s2, b2c = fit_bias(X[tr2] @ w2c, y[tr2])
            loco_clean[cell] = float(np.mean(((s2 * (X[te] @ w2c) + b2c) > 0).astype(int) == y[te]))
    surface_info = {k: {"norm": float(np.linalg.norm(v)), "cos_with_raw_probe": float((v @ w_raw) / (np.linalg.norm(v) * np.linalg.norm(w_raw) + 1e-12))}
                    for k, v in dirs.items()}
    surface_info["cos_raw_vs_clean"] = float(w_raw @ w_clean_unit / (np.linalg.norm(w_raw) + 1e-12))
    # per-layer held-out accuracy at the chosen C, so "is the trait more linear earlier?" is answered directly
    per_layer = {}
    for L2 in pc["layer_candidates"]:
        X2 = z[f"{key}_{L2}"].astype(np.float64); mu2, sd2 = X2[tr].mean(0), X2[tr].std(0) + 1e-6
        w2, b2 = fit_logistic((X2[tr] - mu2) / sd2, y[tr], C)
        per_layer[str(L2)] = {"heldout_acc": accuracy((X2[ho] - mu2) / sd2, y[ho], w2, b2),
                              "cv_acc": cv_score(X2[tr], y[tr], C)}
    rep = {"task": task, "layer": L, "C": C, "w": w.tolist(), "b": float(b), "mu": mu.tolist(), "sigma": sd.tolist(),
           "cv_acc": cv_acc, "heldout_acc": heldout, "heldout_kind": heldout_kind, "per_layer": per_layer,
           "heldout_acc_clean": heldout_clean, "surface_loco": loco, "surface_loco_clean": loco_clean,
           "surface_directions": surface_info, "clean_scale": sc, "clean_bias": bc,
           "n_train": int(len(tr)), "n_heldout": int(len(ho)),
           "position": key, "steering_vector": f"probe_{task}", "fan2026_reference": base["fan2026_reference"]}
    (d / "probe.json").write_text(json.dumps(rep, indent=2))
    store = Path(run_dir) / "steering_vectors.npz"
    vecs = dict(np.load(store)) if store.exists() else {}
    vecs[f"probe_{task}"] = w_raw.astype(np.float32)
    vecs[f"probe_{task}__layer"] = np.array(L)
    vecs[f"probe_{task}_clean"] = w_clean_unit.astype(np.float32)
    vecs[f"probe_{task}_clean__layer"] = np.array(L)
    np.savez(store, **vecs)
    fan = base["fan2026_reference"]
    print(f"[{task}] layer={L} (fan:{fan['probe_layer']}) C={C} cv_acc={cv_acc:.3f} heldout_acc={heldout:.3f} "
          f"[{heldout_kind}] (fan:{fan['heldout_acc']}) n_train={len(tr)} n_heldout={len(ho)} -> steering vector probe_{task}")
    print("   held-out acc by layer: " + " ".join(f"{k}:{v['heldout_acc']:.3f}" for k, v in per_layer.items()))
    print(f"   cleaned direction (surface projected out): heldout_acc_clean={heldout_clean:.3f} cos(raw,clean)={surface_info['cos_raw_vs_clean']:.3f}")
    if loco:
        print("   surface leave-one-cell-out (raw | clean | ceiling): " + " ".join(
            f"{c}:{v['heldout_acc']:.2f}|{loco_clean.get(c, float('nan')):.2f}|{v['ceiling_by_grid']:.2f}" for c, v in loco.items()))
    if heldout < gc["g9_probe_heldout_acc_min"]:
        print(f"STOP: {task} heldout_acc {heldout:.3f} < {gc['g9_probe_heldout_acc_min']}: trait not linearly "
              "accessible at any candidate layer on this model; nothing to calibrate.")
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--mock", action="store_true", help="accepted for symmetry; training is always real")
    a = ap.parse_args()
    pc, gc = _cfg()
    tasks = a.tasks.split(",") if a.tasks else pc["tasks"]
    from probe.synth_trials import task_has_dial
    live = [t for t in tasks if task_has_dial(a.run_dir, t)]
    for t in tasks:
        if t not in live: print(f"[{t}] skipped: no dial on this model (see baseline.json)")
    ok = all([train_task(t, a.run_dir, pc, gc) for t in live]) if live else False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
