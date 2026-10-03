"""Item 6 steering analysis (gate rules 2026-10-03.1; analyze/PREREG_ITEM6_STEERING.md sections 3, 6, 7). Numpy only.

Readouts from first-token logits:
  served      the sampling distribution at T = 0.8, top-p 0.95 (PRIMARY: the distribution the model samples from)
  softmax     the untruncated softmax at T = 0.8 (SENSITIVITY, reported beside every effect: top-p can drop an option from
              the nucleus and put a step in the dose curve that the model's preferences do not have)
  P = q(high) / q(high or low), m = q(high or low) (parseable mass)

Switching point per surface cell: P(n) isotonic-increasing (weighted PAV), sp = linear-interpolated 0.5 crossing, None
when the fitted curve does not cross 0.5 inside the grid.

G4 (primary site only): the placebo-subtracted symmetric effect E_c at lambda*, five criteria; see g4_verdict().
"""
import math

import numpy as np

RULES = "2026-10-03.1"
T_SERVED, TOP_P = 0.8, 0.95
LAMBDA_PREF = 0.4
EFFECT_MIN = 10.0          # tokens, g9_cell_effect_min (D7)
MASS_MIN = 0.95            # coherence (a): mean parseable mass (D4)
MOVE = 0.05                # per-item: |d_i| >= this counts as moved
N_BOOT, BOOT_SEED = 2000, 20261003


# ------------------------------------------------------------------ readouts
def readouts(logits, hi_ids, lo_ids, T=T_SERVED, top_p=TOP_P):
    """logits [B, V] (numpy) -> {"served": (P, m), "softmax": (P, m)} with P, m arrays [B]. Nucleus as the samplers in
    this repo and vLLM: sort descending, keep a token while the cumulative mass BEFORE it is < top_p."""
    z = np.asarray(logits, dtype=np.float64) / T
    z = z - z.max(axis=1, keepdims=True)
    p = np.exp(z); p /= p.sum(axis=1, keepdims=True)
    order = np.argsort(-p, axis=1, kind="stable")
    ps = np.take_along_axis(p, order, axis=1)
    keep_sorted = (np.cumsum(ps, axis=1) - ps) < top_p
    keep = np.zeros_like(keep_sorted)
    np.put_along_axis(keep, order, keep_sorted, axis=1)
    q = p * keep; q /= q.sum(axis=1, keepdims=True)
    out = {}
    for name, dist in (("served", q), ("softmax", p)):
        h = dist[:, list(hi_ids)].sum(1); l = dist[:, list(lo_ids)].sum(1)
        m = h + l
        out[name] = (np.where(m > 0, h / np.maximum(m, 1e-300), np.nan), m)
    return out


# ------------------------------------------------------------------ switching points
def pav_increasing(x, y, w):
    """Weighted isotonic (non-decreasing) fit of y on sorted unique x. Returns fitted values aligned with x."""
    vals, wts, blocks = list(map(float, y)), list(map(float, w)), [[i] for i in range(len(y))]
    i = 0
    while i < len(vals) - 1:
        if vals[i] > vals[i + 1] + 1e-15:
            tot = wts[i] + wts[i + 1]
            vals[i] = (vals[i] * wts[i] + vals[i + 1] * wts[i + 1]) / tot; wts[i] = tot
            blocks[i] += blocks[i + 1]; del vals[i + 1], wts[i + 1], blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    fit = np.empty(len(y))
    for v, b in zip(vals, blocks):
        fit[b] = v
    return fit


def sp_of(ns, ps, weights=None):
    """ns, ps: per-item n and P (any order, repeats allowed). Collapse to unique n (weighted mean), isotonic-increasing
    fit, first 0.5 crossing by linear interpolation. None if the fit stays on one side of 0.5 or ps has NaN."""
    ns = np.asarray(ns, float); ps = np.asarray(ps, float)
    w = np.ones_like(ps) if weights is None else np.asarray(weights, float)
    ok = ~np.isnan(ps)
    if ok.sum() < 2:
        return None
    ns, ps, w = ns[ok], ps[ok], w[ok]
    ux = np.unique(ns)
    uy = np.array([np.average(ps[ns == u], weights=w[ns == u]) for u in ux])
    uw = np.array([w[ns == u].sum() for u in ux])
    f = pav_increasing(ux, uy, uw)
    if f[0] >= 0.5 or f[-1] < 0.5:
        return None
    j = int(np.argmax(f >= 0.5))
    if f[j] == f[j - 1]:
        return float(ux[j])
    return float(ux[j - 1] + (0.5 - f[j - 1]) * (ux[j] - ux[j - 1]) / (f[j] - f[j - 1]))


def cell_sps(items, P, weights=None):
    """items: list of dicts with n, cell. -> {cell: sp}."""
    cells = sorted({it["cell"] for it in items})
    out = {}
    for c in cells:
        idx = [i for i, it in enumerate(items) if it["cell"] == c]
        out[c] = sp_of([items[i]["n"] for i in idx], [P[i] for i in idx], None if weights is None else [weights[i] for i in idx])
    return out


def pooled_sp(items, P, weights=None):
    return sp_of([it["n"] for it in items], P, weights)


# ------------------------------------------------------------------ G4
def _sym(sp_plus, sp_minus):
    return {c: (None if sp_plus.get(c) is None or sp_minus.get(c) is None else sp_plus[c] - sp_minus[c]) for c in sp_plus}


def choose_lambda(lams, sps, target, placebos, coherent, pref=LAMBDA_PREF):
    """The widest symmetric |lambda| <= pref in the grid with both signs coherent for the target and every cell's sp
    inside the grid for the target and every placebo at both signs. sps: {vec: {lam: {cell: sp}}}."""
    cand = sorted({abs(l) for l in lams if 0 < abs(l) <= pref + 1e-9}, reverse=True)
    for l in cand:
        lp, lm = _key(lams, l), _key(lams, -l)
        if lp is None or lm is None or not (coherent.get(lp) and coherent.get(lm)):
            continue
        if all(v is not None for vec in [target] + list(placebos) for lam in (lp, lm) for v in sps[vec][lam].values()):
            return l, lp, lm
    return None


def _key(lams, x):
    for l in lams:
        if abs(l - x) < 1e-9:
            return l
    return None


def effects_at(sps, target, placebos, lp, lm):
    """Per cell: target symmetric effect, each placebo's, E_c = target - mean(placebos)."""
    tv = _sym(sps[target][lp], sps[target][lm])
    pv = {p: _sym(sps[p][lp], sps[p][lm]) for p in placebos}
    E = {c: (None if tv[c] is None or any(pv[p][c] is None for p in placebos) else tv[c] - float(np.mean([pv[p][c] for p in placebos])))
         for c in tv}
    return tv, pv, E


def _boot_pooled_E(items, P_by, target, placebos, lp, lm, n_boot=N_BOOT, seed=BOOT_SEED):
    """Cluster bootstrap over grid points n (all cells of an n move together): pooled E = mean over cells of E_c."""
    rng = np.random.default_rng(seed)
    ns = np.array(sorted({it["n"] for it in items}))
    by_n = {n: [i for i, it in enumerate(items) if it["n"] == n] for n in ns}
    out = []
    for _ in range(n_boot):
        draw = rng.choice(ns, size=len(ns), replace=True)
        idx = np.concatenate([by_n[n] for n in draw])
        sub = [items[i] for i in idx]
        sps = {v: {l: cell_sps(sub, np.asarray(P_by[v][l])[idx]) for l in (lp, lm)} for v in [target] + list(placebos)}
        _, _, E = effects_at(sps, target, placebos, lp, lm)
        vals = [e for e in E.values() if e is not None]
        if len(vals) == len(E):
            out.append(float(np.mean(vals)))
    if not out:
        return None, None, 0
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5)), len(out)


def monotone_ok(curve, expected_sign=-1, dip_frac=0.10):
    """G4's coherent() (10 % dip tolerance) plus the expected direction: sp(max lambda) - sp(min lambda) has the expected sign."""
    ks = sorted(curve)
    if len(ks) < 2:
        return False
    vals = [curve[k] for k in ks]
    tol = dip_frac * abs(vals[-1] - vals[0]) + 1e-6
    if expected_sign < 0:
        mono = all(y <= x + tol for x, y in zip(vals, vals[1:]))
    else:
        mono = all(y >= x - tol for x, y in zip(vals, vals[1:]))
    return bool(mono and np.sign(vals[-1] - vals[0]) == np.sign(expected_sign))


def g4_verdict(items, P_by, target, placebos, lams, coherent, instrument_ok=True, n_boot=N_BOOT, expected_sign=-1):
    """items: the reference-level items; P_by: {vec: {lam: P array aligned with items}} (lambda 0 under every vec = the
    unsteered P). coherent: {lam: bool} for the target. Returns the verdict dict (PASS / FAIL / NOT_EVALUABLE)."""
    sps = {v: {l: cell_sps(items, P_by[v][l]) for l in lams} for v in [target] + list(placebos)}
    cells = sorted({it["cell"] for it in items}); nc = len(cells)
    res = {"rules": RULES, "target": target, "n_placebos": len(placebos), "cells": cells, "sp": sps}
    if not instrument_ok:
        return {**res, "verdict": "NOT_EVALUABLE", "reason": "an instrument check failed"}
    ch = choose_lambda(lams, sps, target, placebos, coherent)
    if ch is None:
        return {**res, "verdict": "NOT_EVALUABLE", "reason": "no symmetric coherent lambda <= 0.4 with every cell inside the grid"}
    l, lp, lm = ch
    tv, pv, E = effects_at(sps, target, placebos, lp, lm)
    med = float(np.median([E[c] for c in cells]))
    neg = sum(1 for c in cells if E[c] * expected_sign > 0)
    beat_all = sum(1 for c in cells if all(abs(tv[c]) > abs(pv[p][c]) for p in placebos))     # magnitude (D5); sign is criteria 1-2
    lo, hi, nb = _boot_pooled_E(items, P_by, target, placebos, lp, lm, n_boot)
    pooled = float(np.mean([E[c] for c in cells]))
    # dose curve: pooled placebo-subtracted sp over coherent lambdas where every cell is defined for target and placebos
    dose = {}
    for lam in lams:
        if not coherent.get(lam):
            continue
        if all(sps[v][lam][c] is not None for v in [target] + list(placebos) for c in cells):
            dose[lam] = float(np.mean([sps[target][lam][c] - np.mean([sps[p][lam][c] for p in placebos]) for c in cells]))
    crit = {
        "1_median_E_le_-10": bool(med * expected_sign >= EFFECT_MIN),
        "2_sign_agree_ge_n-1": bool(neg >= nc - 1),
        "3_beats_every_placebo_ge_n-1": bool(beat_all >= nc - 1),
        "4_pooled_ci_excludes_0": bool(lo is not None and (hi < 0 if expected_sign < 0 else lo > 0)),
        "5_dose_monotone": monotone_ok(dose, expected_sign),
    }
    return {**res, "lambda_star": l, "E_by_cell": E, "target_effect_by_cell": tv, "placebo_effect_by_cell": pv,
            "median_E": med, "pooled_E": pooled, "pooled_E_ci95": [lo, hi], "boot_draws": nb, "sign_agree": neg,
            "beats_every_placebo": beat_all, "dose_curve": dose, "criteria": crit,
            "verdict": "PASS" if all(crit.values()) else "FAIL"}


# ------------------------------------------------------------------ per-item shifts (descriptive, D6)
def per_item(items, P_by, target, placebos, lam, expected_sign=-1):
    """d_i(lam) = [P_v(lam) - P_v(0)] - mean_p [P_p(lam) - P_p(0)]. Expected movement of P(high): + for +lam.
    (expected_sign is the sp direction; P(high) moves opposite to sp.)"""
    zero = 0.0
    dv = np.asarray(P_by[target][lam]) - np.asarray(P_by[target][zero])
    dp = np.mean([np.asarray(P_by[p][lam]) - np.asarray(P_by[p][zero]) for p in placebos], axis=0) if placebos else 0.0
    d = dv - dp
    want = np.sign(lam) * (-expected_sign)          # +lam should raise P(high) when sp falls with lam
    p0 = np.asarray(P_by[target][zero])
    at_target = (p0 >= 0.9) if want > 0 else (p0 <= 0.1)
    moved = np.abs(d) >= MOVE
    wrong = moved & (np.sign(d) == -want)
    q = np.nanpercentile(d, [5, 25, 50, 75, 95]).tolist()
    by_cell = {}
    for c in sorted({it["cell"] for it in items}):
        ix = np.array([i for i, it in enumerate(items) if it["cell"] == c])
        by_cell[c] = {"median": float(np.nanmedian(d[ix])), "moved": float(moved[ix].mean()), "wrong_way": float(wrong[ix].mean())}
    return {"lambda": lam, "quantiles_5_25_50_75_95": q, "share_moved": float(moved.mean()), "share_wrong_way": float(wrong.mean()),
            "n_at_target_side": int(at_target.sum()),
            "mean_d_at_target_side": (float(np.nanmean(d[at_target])) if at_target.any() else None), "by_cell": by_cell}


def fmt(x, nd=1):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"
