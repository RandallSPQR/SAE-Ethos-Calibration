#!/usr/bin/env python3
"""Item 6 steering vectors (gate rules 2026-10-03.1; analyze/PREREG_ITEM6_STEERING.md, section 2). Offline, numpy only.

From a run's NATIVE prompt-final activations (probe/<task>/activations.npz), per steering site (task, layer):
  probe_clean   as probe.train / probe.transfer: z-scored L2 logistic, C by 5-fold CV, raw-space unit direction,
                surface directions (order, unit; matched on grid point and label) projected out
  (MoD was dropped from item 6 by Randall's ruling C, 2026-10-03: prompt-final activations are prompt-deterministic, so
   every MoD is a between-prompt contrast; mod_matched / mod_frame_matched stay below as the record of why.)
  placebo_iso<k>, placebo_cov<k>   unit vectors, isotropic (uniform on the sphere) and covariance-matched (x ~ N(0, S_L),
                S_L from the centered training-split activations); seeds 61000 + 100 k + L and 62000 + 100 k + L
Orientation: + points to the high class (Risky Option / Accept) for every target vector.

Descriptives for probe_clean (no gate; PREREG_ITEM6_STEERING.md section 2.4): its cosine with the n direction estimated
from choice-HOMOGENEOUS prompts only, and AUROC pooled across safe levels (where raw n and the choice dissociate).

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
    """Option A for the MoD blocker (PREREG_ITEM6_STEERING.md 2.2; NOT used: ruling C dropped MoD, A is confounded with n): strata = (level, order, unit), i.e.
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


def _f(x):
    return "-" if x is None else f"{x:.3f}"


HOMOG_BELOW, HOMOG_ABOVE = 0.75, 1.25       # grid points at <= 0.75 x / >= 1.25 x the level's switching point


def _sp_by_level(task, run_dir):
    """{level as stored in activations.npz: native switching point}. The ultimatum's single level is stored as -1."""
    base = json.loads((Path(run_dir) / "probe" / task / "baseline.json").read_text())
    return {(-1.0 if k == "None" else float(k)): v["sp"] for k, v in base["sp_by_level"].items()}


def n_direction_homogeneous(X, z, y, task, run_dir, region):
    """The n direction where the choice does not vary: grid points (level, n) whose native trials ALL chose low and
    n <= 0.75 x the level's switching point ("below"), or ALL chose high and n >= 1.25 x it ("above"). Within those
    prompts, X and n are centered per level and the least-squares slope of X on n is the direction (unit). A probe that
    reads the choice should be near-orthogonal to it; one that reads the number should not. None if fewer than 3
    distinct n qualify."""
    spl = _sp_by_level(task, run_dir)
    lv, par = z["level"].astype(float), z["param"].astype(float)
    idx, used = [], []
    for lvl, n in sorted(set(zip(lv.tolist(), par.tolist()))):
        sp = spl.get(lvl)
        if sp is None:
            continue
        m = np.where((lv == lvl) & (par == n))[0]
        ys = y[m]
        if (region == "below" and n <= HOMOG_BELOW * sp and ys.max() == 0) or (region == "above" and n >= HOMOG_ABOVE * sp and ys.min() == 1):
            idx.append(m); used.append((lvl, n))
    if len({u[1] for u in used}) < 3:
        return None, {"grid_points": len(used)}
    idx = np.concatenate(idx)
    Xc, nc = X[idx].copy(), par[idx].copy()
    for l in set(lv[idx].tolist()):
        mm = lv[idx] == l
        Xc[mm] -= Xc[mm].mean(0); nc[mm] -= nc[mm].mean()
    return _unit(Xc.T @ nc), {"grid_points": len(used), "trials": int(len(idx)), "levels": sorted({u[0] for u in used})}


def descriptives(w, X, z, y, task, run_dir):
    """No gate. cos(probe_clean, homogeneous n direction) below and above the switching point (random |cos| 99th pct at
    d = 5376 is ~0.035), and AUROC of the probe score, raw n and n / safe, pooled over every native trial (all safe
    levels: where raw n and the choice dissociate) and on the held-out level alone."""
    from probe import transfer as T
    from probe.tasks import TASKS
    out = {"cos_n_direction": {}, "auroc_pooled": {}, "auroc_heldout_level": {}}
    for region in ("below", "above"):
        d, info = n_direction_homogeneous(X, z, y, task, run_dir, region)
        out["cos_n_direction"][region] = {**info, "cos": None if d is None else float(w @ d)}
    s = X @ w; n = z["param"].astype(float)
    safe = z["level"].astype(float) if TASKS[task]["levels"] != [None] else None
    out["auroc_pooled"] = {"probe_clean": T.auroc(s, y), "n": T.auroc(n, y),
                           "n_over_safe": None if safe is None else T.auroc(n / safe, y), "trials": int(len(y)),
                           "levels": None if safe is None else sorted({float(a) for a in safe})}
    ho = TASKS[task].get("heldout_level")
    if ho is not None:
        m = safe == float(ho)
        out["auroc_heldout_level"] = {"level": ho, "probe_clean": T.auroc(s[m], y[m]), "n": T.auroc(n[m], y[m]), "trials": int(m.sum())}
    return out


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
        Xc = X[tr] - X[tr].mean(0)
        vecs = {"probe_clean": w_clean}
        for k in range(1, k_per + 1):
            vecs[f"placebo_iso{k}"] = placebo_iso(X.shape[1], iso_seed(k, L))
            vecs[f"placebo_cov{k}"] = placebo_cov(Xc, cov_seed(k, L))
        for kind, v in vecs.items():
            nm = vec_name(task, L, kind)
            store[nm] = v.astype(np.float32); store[nm + "__layer"] = np.array(L)
            man["vectors"][nm] = {"task": task, "layer": L, "kind": kind, "sha256": sha(store[nm]),
                                  **({"seed": iso_seed(int(kind[11:]), L)} if kind.startswith("placebo_iso") else {}),
                                  **({"seed": cov_seed(int(kind[11:]), L)} if kind.startswith("placebo_cov") else {})}
        man["sites"][f"{task}_L{L}"] = {
            "task": task, "layer": L, "role": role, "C": C, "n_train": int(len(tr)),
            "placebos": {"isotropic": k_per, "covariance": k_per},
            "cos_raw_clean": float(_unit(w_raw) @ w_clean),
            "transfer": {"probe_clean": mod_transfer(w_clean, run_dir, task, L, key, n_boot)},
            "descriptives": descriptives(w_clean, X, z, y, task, run_dir)}
        s = man["sites"][f"{task}_L{L}"]; ds = s["descriptives"]
        print(f"[{task} L{L} {role}] transfer {s['transfer']['probe_clean']['agent_auroc']:.3f} {s['transfer']['probe_clean']['verdict']}  "
              f"cos(probe_clean, n-dir homogeneous below / above) {_f(ds['cos_n_direction']['below'].get('cos'))} / "
              f"{_f(ds['cos_n_direction']['above'].get('cos'))}  AUROC pooled: probe {_f(ds['auroc_pooled']['probe_clean'])} "
              f"n {_f(ds['auroc_pooled']['n'])} n/safe {_f(ds['auroc_pooled']['n_over_safe'])}", flush=True)
    p = out_dir / "steering_vectors.npz"
    np.savez(p, **store)
    man["npz_sha256"] = file_sha(p)
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
