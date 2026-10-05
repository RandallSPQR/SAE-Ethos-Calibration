#!/usr/bin/env python3
"""Item 6b offline diagnostics (Randall 2026-10-05; $0, descriptive; no pod). Per frozen steering site, from run 2's native
prompt-final activations:

Vectors (all unit-normalized; + toward the high class):
  probe_clean   the frozen item 6 vector (raw-space logistic filter w/sd, surface directions projected out)
  pattern       a = S w_clean (Haufe et al. 2014 activation pattern), S = covariance of the centered training split,
                computed as Xc^T (Xc w) / (n - 1) without forming S
  pattern_raw   a = S w_raw (the pattern of the uncleaned filter)
  fan           Fan et al. 2026's steering vector: the logistic weight learned on z-scored features, unit-normalized
                and added to the raw hidden state as written in their method (no inverse transform stated)
  g9_raw, g9_clean   the vectors probe.train (the G9 path) stores, when --g9 points at its steering_vectors.npz
  placebo_iso1, placebo_cov1   the frozen placebos, for scale

Cosines with: the n direction from choice-homogeneous prompts (below / above the switching point; probe.vectors), the
all-prompt within-level n slope, MoD matched on the grid point and MoD matched on the frame (probe.vectors; both dropped
from item 6, compared here only). Nulls for |cos(., n)|: 99th percentile over 1,000 isotropic directions (the "magnitude
null") and over 1,000 covariance-matched directions.
STOP flag (Randall, item 6b (1)): |cos(pattern, n-direction homogeneous)| above the magnitude (isotropic) null.

Scale (the 6b strength unit is the natural-projection sd of each vector): sd along v; max per-dimension push of a 1-sd
step along v, in that dimension's own sds; low-variance share (|v|^2 on the 10 % lowest-variance dims); and the item 6
metric (max per-dim push at lambda = 0.1 x mean norm).

  python -m probe.pattern_diag --run-dir results/t4_27b_2026-10-02_probe_transfer_run2 \
      --vectors results/t4_27b_2026-10-03_steering_vectors [--g9 <probe.train steering_vectors.npz>] --out <dir>
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

N_NULL, NULL_SEED = 1000, 80000


def _u(v):
    return v / (np.linalg.norm(v) + 1e-12)


def scale(X, v, sd, mn, low):
    v = _u(v)
    s = float((X @ v).std())
    return {"sd_along_v": s, "lambda_for_1sd": s / mn, "low_variance_share": float(np.sum(v[low] ** 2)),
            "max_dim_push_sd_at_1sd_step": float(np.max(s * np.abs(v) / sd)),
            "max_dim_push_sd_at_lambda_0.1": float(np.max(0.1 * mn * np.abs(v) / sd))}


def nulls(d, Xc, target, seed):
    rng = np.random.default_rng(seed)
    t = _u(target)
    iso = rng.normal(size=(N_NULL, d)); iso /= np.linalg.norm(iso, axis=1, keepdims=True)
    cov = rng.normal(size=(N_NULL, Xc.shape[0])) @ Xc; cov /= np.linalg.norm(cov, axis=1, keepdims=True)
    return float(np.percentile(np.abs(iso @ t), 99)), float(np.percentile(np.abs(cov @ t), 99))


def run(run_dir, vectors_dir, g9=None):
    import modelcfg
    from probe import vectors as VE
    from probe.train import fit_logistic, surface_directions, orthogonalize
    V = np.load(Path(vectors_dir) / "steering_vectors.npz")
    man = json.loads((Path(vectors_dir) / "vectors_manifest.json").read_text())
    G = np.load(g9) if g9 else None
    out = {"sites": {}}
    for site, s in man["sites"].items():
        task, L = s["task"], s["layer"]
        z = np.load(Path(run_dir) / "probe" / task / "activations.npz")
        X = z[f"X_{L}"].astype(np.float64); y = z["y"].astype(int)
        tr = VE.training_split(task, z)
        Xt = X[tr]; mu, sdt = Xt.mean(0), Xt.std(0) + 1e-6
        Xc = Xt - mu
        w_z, _ = fit_logistic((Xt - mu) / sdt, y[tr].astype(float), float(s["C"]))
        w_raw = _u(w_z / sdt)
        w_clean = V[f"{site}_probe_clean"].astype(np.float64)
        vecs = {"probe_clean": w_clean,
                "pattern": _u(Xc.T @ (Xc @ w_clean) / (len(tr) - 1)),
                "pattern_raw": _u(Xc.T @ (Xc @ w_raw) / (len(tr) - 1)),
                "fan": _u(w_z),
                "placebo_iso1": V[f"{site}_placebo_iso1"].astype(np.float64),
                "placebo_cov1": V[f"{site}_placebo_cov1"].astype(np.float64)}
        if G is not None and task == "lottery" and int(G[f"probe_{task}__layer"]) == L:
            vecs["g9_raw"] = G[f"probe_{task}"].astype(np.float64); vecs["g9_clean"] = G[f"probe_{task}_clean"].astype(np.float64)
        # reference directions
        refs = {}
        for region in ("below", "above"):
            dvec, info = VE.n_direction_homogeneous(X, z, y, task, run_dir, region)
            refs[f"n_homog_{region}"] = (dvec, info)
        lv, par = z["level"].astype(float)[tr], z["param"].astype(float)[tr]
        Xw, nw = Xt.copy(), par.copy()
        for l in np.unique(lv):
            m = lv == l; Xw[m] -= Xw[m].mean(0); nw[m] -= nw[m].mean()
        refs["n_slope_all"] = (_u(Xw.T @ nw), {"trials": int(len(tr))})
        dirs = surface_directions(X, z, tr, y.astype(float)) if "order" in z.files else {}
        g_mod, _ = VE.mod_matched(X, z, y, tr)
        refs["mod_grid_matched_clean"] = (_u(orthogonalize(_u(g_mod), dirs)[0]), {})
        if "order" in z.files:
            f_mod, _ = VE.mod_frame_matched(X, z, y, tr)
            refs["mod_frame_matched"] = (_u(f_mod), {})
        sd = X.std(0) + 1e-9; mn = float(np.linalg.norm(X, axis=1).mean()); low = np.argsort(sd)[: len(sd) // 10]
        site_out = {"task": task, "layer": L, "role": s["role"], "nulls": {}, "cos": {}, "scale": {}}
        for rk, (rv, info) in refs.items():
            if rv is None:
                site_out["nulls"][rk] = {"undefined": info}
                continue
            iso99, cov99 = nulls(X.shape[1], Xc, rv, NULL_SEED + L)
            site_out["nulls"][rk] = {"iso_p99": iso99, "cov_p99": cov99, **info}
        for vk, v in vecs.items():
            site_out["cos"][vk] = {rk: (None if rv is None else float(_u(v) @ rv)) for rk, (rv, _) in refs.items()}
            site_out["cos"][vk]["probe_clean"] = float(_u(v) @ _u(w_clean))
            site_out["scale"][vk] = scale(X, v, sd, mn, low)
        hom = [rk for rk in ("n_homog_below", "n_homog_above") if site_out["cos"]["pattern"].get(rk) is not None]
        site_out["stop_pattern_vs_magnitude_null"] = {rk: abs(site_out["cos"]["pattern"][rk]) > site_out["nulls"][rk]["iso_p99"] for rk in hom}
        out["sites"][site] = site_out
    out["STOP"] = any(any(v.values()) for v in (s["stop_pattern_vs_magnitude_null"] for s in out["sites"].values()))
    return out


def render(r):
    f = lambda x, n=3: "-" if x is None else f"{x:.{n}f}"
    L = ["# Item 6b offline diagnostics (descriptive; $0)", "",
         f"**STOP flag (|cos(pattern, homogeneous n direction)| > the isotropic magnitude null at any site): {r['STOP']}**", ""]
    for site, s in r["sites"].items():
        L += [f"## {site} ({s['role']})", "", "Cosines (columns: reference directions; nulls are 99th percentiles of |cos| with that reference):", "",
              "| vector | " + " | ".join(s["nulls"]) + " | probe_clean |", "|---|" + "---|" * (len(s["nulls"]) + 1)]
        L.append("| *null iso / cov* | " + " | ".join(("-" if "undefined" in n else f"{f(n['iso_p99'])} / {f(n['cov_p99'])}") for n in s["nulls"].values()) + " | |")
        for vk, c in s["cos"].items():
            L.append(f"| {vk} | " + " | ".join(f(c.get(rk)) for rk in s["nulls"]) + f" | {f(c['probe_clean'])} |")
        L += ["", "Scale (the 6b unit is a 1-sd step along each vector's own natural projection):", "",
              "| vector | sd along v | lambda for 1 sd (x mean norm) | max per-dim push of a 1-sd step (dim sds) | low-variance share | max per-dim push at lambda 0.1 (item 6) |",
              "|---|---|---|---|---|---|"]
        for vk, sc in s["scale"].items():
            L.append(f"| {vk} | {sc['sd_along_v']:.1f} | {sc['lambda_for_1sd']:.5f} | {sc['max_dim_push_sd_at_1sd_step']:.2f} | "
                     f"{sc['low_variance_share']:.3f} | {sc['max_dim_push_sd_at_lambda_0.1']:.0f} |")
        L += ["", f"STOP check for this site: {s['stop_pattern_vs_magnitude_null']}", ""]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True); ap.add_argument("--vectors", required=True)
    ap.add_argument("--g9"); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    r = run(a.run_dir, a.vectors, a.g9)
    o = Path(a.out); o.mkdir(parents=True, exist_ok=True)
    (o / "diagnostics.json").write_text(json.dumps(r, indent=1))
    (o / "DIAGNOSTICS.md").write_text(render(r))
    print(render(r))


if __name__ == "__main__":
    main()
