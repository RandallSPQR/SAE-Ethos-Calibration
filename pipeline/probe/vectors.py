#!/usr/bin/env python3
"""Item 6 steering vectors (gate rules 2026-10-03.1; analyze/PREREG_ITEM6_STEERING.md, section 2). Offline, numpy only.

From a run's NATIVE prompt-final activations (probe/<task>/activations.npz), per steering site (task, layer):
  probe_clean   as probe.train / probe.transfer: z-scored L2 logistic, C by 5-fold CV, raw-space unit direction,
                surface directions (order, unit; matched on grid point and label) projected out
  mod_clean     mean of differences matched on the grid point: within each (level, n) stratum holding both choices,
                mean(x | high) - mean(x | low); averaged with weights min(n_high, n_low); surface directions projected
                out; unit-normalized
  placebo_iso<k>, placebo_cov<k>   unit vectors, isotropic (uniform on the sphere) and covariance-matched (x ~ N(0, S_L),
                S_L from the centered training-split activations); seeds 61000 + 100 k + L and 62000 + 100 k + L
Orientation: + points to the high class (Risky Option / Accept) for every target vector.

Then the MoD use gate (a): gate rules 2026-10-02.1 applied to the mod_clean direction on the run's AGENT activations.

  python -m probe.vectors --run-dir <run2 dir> --out <dir>      writes steering_vectors.npz + vectors_manifest.json
  python -m probe.vectors --verify <dir>                         re-hashes the npz against its manifest (exit 1 on mismatch)
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RULES = "2026-10-03.1"
# (task, layer, role, placebos per kind): lottery L38 = 8 isotropic + 8 covariance-matched (D5); the other sites 2 + 2
SITES = [("lottery", 38, "primary", 8), ("lottery", 30, "secondary", 2),
         ("ultimatum", 40, "exploratory", 2), ("ultimatum", 46, "exploratory", 2)]
ISO_SEED, COV_SEED = 61000, 62000


def iso_seed(k, L):
    return ISO_SEED + 100 * k + L


def cov_seed(k, L):
    return COV_SEED + 100 * k + L


def vec_name(task, L, kind):
    return f"{task}_L{L}_{kind}"


def _unit(v):
    return v / (np.linalg.norm(v) + 1e-12)


def training_split(task, z):
    from probe.tasks import TASKS
    ho = TASKS[task].get("heldout_level")
    lv = z["level"]
    return np.where(lv != ho)[0] if ho is not None else np.arange(len(z["y"]))


def probe_clean(X, z, y, tr, c_grid):
    """Same construction as probe.transfer.run (so the transfer verdict applies to this exact vector)."""
    from probe.train import fit_logistic, cv_score, surface_directions, orthogonalize
    C = max(c_grid, key=lambda c: cv_score(X[tr], y[tr], float(c)))
    mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6
    w, _ = fit_logistic((X[tr] - mu) / sd, y[tr], float(C))
    w_raw = _unit(w / sd)
    dirs = surface_directions(X, z, tr, y) if "order" in z.files else {}
    w_clean, _ = orthogonalize(w_raw, dirs)
    return _unit(w_clean), w_raw, dirs, float(C)


def mod_matched(X, z, y, tr):
    """Grid-point-matched difference of means. Returns (unit direction, n strata used)."""
    lv, par = z["level"][tr], z["param"][tr]
    Xt, yt = X[tr], y[tr]
    diffs, wts = [], []
    for key in sorted(set(zip(lv.tolist(), par.tolist())), key=str):
        m = (lv == key[0]) & (par == key[1])
        hi, lo = Xt[m & (yt == 1)], Xt[m & (yt == 0)]
        if len(hi) and len(lo):
            diffs.append(hi.mean(0) - lo.mean(0)); wts.append(min(len(hi), len(lo)))
    if not diffs:
        raise RuntimeError("no stratum holds both choices: MoD undefined")
    return np.average(np.stack(diffs), axis=0, weights=np.array(wts, float)), len(diffs)


def mod_frame_matched(X, z, y, tr):
    """Option A for the MoD blocker (PREREG_ITEM6_STEERING_DRAFT 2.2, pending Randall): strata = (level, order, unit), i.e.
    the frame and the safe amount held fixed and n left free; within each stratum holding both choices
    mean(x | high) - mean(x | low); weights min(n_high, n_low). Returns (direction, n strata used)."""
    lv, od, un = z["level"][tr], z["order"][tr], z["unit"][tr]
    Xt, yt = X[tr], y[tr]
    diffs, wts = [], []
    for key in sorted(set(zip(lv.tolist(), od.tolist(), un.tolist())), key=str):
        m = (lv == key[0]) & (od == key[1]) & (un == key[2])
        hi, lo = Xt[m & (yt == 1)], Xt[m & (yt == 0)]
        if len(hi) and len(lo):
            diffs.append(hi.mean(0) - lo.mean(0)); wts.append(min(len(hi), len(lo)))
    if not diffs:
        raise RuntimeError("no frame stratum holds both choices: MoD undefined")
    return np.average(np.stack(diffs), axis=0, weights=np.array(wts, float)), len(diffs)


def placebo_iso(d, seed):
    return _unit(np.random.default_rng(seed).normal(size=d))


def placebo_cov(Xc, seed):
    """x ~ N(0, S) with S = Xc^T Xc / (n - 1), drawn as Xc^T g / sqrt(n - 1), g ~ N(0, I_n): no d x d matrix."""
    g = np.random.default_rng(seed).normal(size=Xc.shape[0])
    return _unit(Xc.T @ g / np.sqrt(max(1, Xc.shape[0] - 1)))


def n_direction(X, z, tr):
    """Unit least-squares slope of the activations on the prompt's n (descriptive: how much of a vector is the number)."""
    n = z["param"][tr].astype(float); n = n - n.mean()
    return _unit((X[tr] - X[tr].mean(0)).T @ n)


def mod_transfer(direc, run_dir, task, L, key, n_boot=2000):
    """Gate rules 2026-10-02.1 on a fixed direction: agent-regime AUROC >= 0.70 and 95 % cluster-bootstrap lower bound > 0.50."""
    from probe import transfer as T
    za = np.load(Path(run_dir) / "probe_agent" / task / "activations.npz")
    Xa, ya = za[f"{key}_{L}"].astype(np.float64), za["y"].astype(int)
    clusters = np.array([f"{l}:{p}" for l, p in zip(za["level"], za["param"])])
    s = Xa @ direc
    a = T.auroc(s, ya); lo, hi = T.cluster_boot(s, ya, clusters, n_boot)
    return {"agent_auroc": a, "agent_ci95": [lo, hi], "verdict": T.verdict(a, lo, ya), "n_agent": int(len(ya))}


def sha(arr):
    return hashlib.sha256(np.ascontiguousarray(arr, dtype="<f4").tobytes()).hexdigest()


def file_sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build(run_dir, out_dir, sites=SITES, n_boot=2000):
    import modelcfg
    pc = modelcfg.probe_cfg()
    key = "Xfirst" if pc.get("position") == "first_answer_token" else "X"
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    store, man = {}, {"rules": RULES, "run_dir": str(run_dir), "position": key, "sites": {}, "vectors": {}}
    for task, L, role, k_per in sites:
        z = np.load(Path(run_dir) / "probe" / task / "activations.npz")
        X, y = z[f"{key}_{L}"].astype(np.float64), z["y"].astype(int)
        tr = training_split(task, z)
        w_clean, w_raw, dirs, C = probe_clean(X, z, y.astype(float), tr, pc["c_grid"])
        mod_raw, n_strata = mod_matched(X, z, y, tr)
        from probe.train import orthogonalize
        mod_clean, _ = orthogonalize(_unit(mod_raw), dirs)
        mod_clean = _unit(mod_clean)
        # unmatched class-mean difference and the n direction: descriptive only
        unmatched = _unit(X[tr][y[tr] == 1].mean(0) - X[tr][y[tr] == 0].mean(0))
        ndir = n_direction(X, z, tr)
        Xc = X[tr] - X[tr].mean(0)
        vecs = {"probe_clean": w_clean, "mod_clean": mod_clean}
        for k in range(1, k_per + 1):
            vecs[f"placebo_iso{k}"] = placebo_iso(X.shape[1], iso_seed(k, L))
            vecs[f"placebo_cov{k}"] = placebo_cov(Xc, cov_seed(k, L))
        for kind, v in vecs.items():
            nm = vec_name(task, L, kind)
            store[nm] = v.astype(np.float32); store[nm + "__layer"] = np.array(L)
            man["vectors"][nm] = {"task": task, "layer": L, "kind": kind, "sha256": sha(store[nm]),
                                  **({"seed": iso_seed(int(kind[11:]), L)} if kind.startswith("placebo_iso") else {}),
                                  **({"seed": cov_seed(int(kind[11:]), L)} if kind.startswith("placebo_cov") else {})}
        cos = lambda a, b: float(_unit(a) @ _unit(b))
        man["sites"][f"{task}_L{L}"] = {
            "task": task, "layer": L, "role": role, "C": C, "n_train": int(len(tr)), "mod_strata": n_strata,
            "placebos": {"isotropic": k_per, "covariance": k_per},
            "cos": {"mod_clean__probe_clean": cos(mod_clean, w_clean), "probe_raw__probe_clean": cos(w_raw, w_clean),
                    "unmatched_mod__n_direction": cos(unmatched, ndir), "mod_clean__n_direction": cos(mod_clean, ndir),
                    "probe_clean__n_direction": cos(w_clean, ndir), "unmatched_mod__mod_clean": cos(unmatched, mod_clean)},
            "transfer": {"probe_clean": mod_transfer(w_clean, run_dir, task, L, key, n_boot),
                         "mod_clean": mod_transfer(mod_clean, run_dir, task, L, key, n_boot)}}
        s = man["sites"][f"{task}_L{L}"]
        print(f"[{task} L{L} {role}] cos(MoD, probe) {s['cos']['mod_clean__probe_clean']:.3f}  "
              f"cos(unmatched MoD, n-dir) {s['cos']['unmatched_mod__n_direction']:.3f}  "
              f"transfer probe {s['transfer']['probe_clean']['agent_auroc']:.3f} {s['transfer']['probe_clean']['verdict']}  "
              f"MoD {s['transfer']['mod_clean']['agent_auroc']:.3f} {s['transfer']['mod_clean']['verdict']}", flush=True)
    p = out_dir / "steering_vectors.npz"
    np.savez(p, **store)
    man["npz_sha256"] = file_sha(p)
    man["mod_allowed_by_transfer"] = {k: v["transfer"]["mod_clean"]["verdict"] == "PASS" for k, v in man["sites"].items()}
    (out_dir / "vectors_manifest.json").write_text(json.dumps(man, indent=1))
    return man


def verify(out_dir, expected_npz_sha=None):
    """Every vector's sha256 and the npz's own sha256 match the manifest (and the registered value when given)."""
    out_dir = Path(out_dir)
    man = json.loads((out_dir / "vectors_manifest.json").read_text())
    z = np.load(out_dir / "steering_vectors.npz")
    bad = [k for k, v in man["vectors"].items() if sha(z[k]) != v["sha256"]]
    fs = file_sha(out_dir / "steering_vectors.npz")
    if fs != man["npz_sha256"]:
        bad.append("npz file")
    if expected_npz_sha and fs != expected_npz_sha:
        bad.append(f"npz != registered {expected_npz_sha[:12]}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir"); ap.add_argument("--out"); ap.add_argument("--verify"); ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    if a.verify:
        bad = verify(a.verify)
        print("vectors verified" if not bad else f"MISMATCH: {bad}")
        sys.exit(1 if bad else 0)
    build(a.run_dir, a.out, n_boot=a.boot)


if __name__ == "__main__":
    main()
