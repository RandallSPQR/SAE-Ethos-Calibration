"""Per-surface-cell dial effect with an interval (rules 2026-09-17.2). Pure Python so the gate fixture
can use it without numpy.

A pooled switching-point curve is a mixture across surface cells (order x unit), so the instrument claim
is made per cell: effect_c = sp_c(+lam) - sp_c(-lam) with SE_c = sqrt(SE_c(+lam)^2 + SE_c(-lam)^2) from
the within-grid-point bootstrap that probe.calibrate stores per cell and lambda. lam is the widest
symmetric |lambda| <= lam_pref at which EVERY cell's switching point lies inside the grid at both ends
(a saturated cell has no finite effect). Evaluability: every cell needs at least min_n trials per grid
point and a finite SE; below that the interval carries no information and the answer is NOT_EVALUABLE,
never pass or fail.
"""
import math
import statistics as st

Z95 = 1.959964


def _key(curve_by_cell, lam):
    for k in curve_by_cell:
        if abs(float(k) - lam) < 1e-9:
            return k
    return None


def cell_effects(dial, lam_pref=0.4, min_n=6, min_cells=6, z=Z95):
    """dial: the calibration.json `dials.<name>` block. Returns a dict with `evaluable`, `reason`,
    `lambda`, `cells` {name: {effect, se, n, lo, hi, excludes_zero}}, `median`, `median_abs`,
    `sign_agree` (cells whose sign matches the median's), `n_cells`, `all_exclude_zero`."""
    bc = dial.get("curve_by_cell") or {}
    det = dial.get("cell_detail_by_lambda") or {}
    out = {"evaluable": False, "reason": None, "lambda": None, "cells": {}, "median": None, "median_abs": None,
           "sign_agree": 0, "n_cells": 0, "all_exclude_zero": False}
    lams = sorted({abs(float(k)) for k in bc if abs(float(k)) > 1e-9 and abs(float(k)) <= lam_pref + 1e-9}, reverse=True)
    if not lams:
        out["reason"] = "no symmetric lambda pair in the sweep"
        return out
    chosen = None
    for l in lams:
        kh, kl = _key(bc, l), _key(bc, -l)
        if kh is None or kl is None:
            continue
        hi, lo = bc[kh] or {}, bc[kl] or {}
        cells = sorted(set(hi) & set(lo))
        if cells and all(hi[c] is not None and lo[c] is not None for c in cells):
            chosen = (l, kh, kl, cells); break
    if chosen is None:
        out["reason"] = "no symmetric lambda at which every cell's switching point is inside the grid"
        return out
    l, kh, kl, cells = chosen
    out["lambda"] = l
    n_cells_total = len(cells)
    sweep_agents = dial.get("sweep_agents")
    fallback_n = (sweep_agents // max(1, n_cells_total)) if sweep_agents else None
    for c in cells:
        dh, dl = (det.get(kh) or {}).get(c) or {}, (det.get(kl) or {}).get(c) or {}
        eff = float(bc[kh][c]) - float(bc[kl][c])
        se_h, se_l = dh.get("se"), dl.get("se")
        se = math.sqrt(se_h ** 2 + se_l ** 2) if (se_h is not None and se_l is not None) else None
        n = min(x for x in (dh.get("n_per_point"), dl.get("n_per_point")) if x is not None) \
            if (dh.get("n_per_point") is not None or dl.get("n_per_point") is not None) else fallback_n
        row = {"effect": eff, "se": se, "n": n, "lo": None, "hi": None, "excludes_zero": None}
        if se is not None:
            row["lo"], row["hi"] = eff - z * se, eff + z * se
            row["excludes_zero"] = bool(abs(eff) > z * se)
        out["cells"][c] = row
    out["n_cells"] = len(out["cells"])
    effs = [r["effect"] for r in out["cells"].values()]
    med = st.median(effs)
    out["median"], out["median_abs"] = med, st.median([abs(e) for e in effs])
    sgn = (med > 0) - (med < 0)
    out["sign_agree"] = sum(1 for e in effs if ((e > 0) - (e < 0)) == sgn and sgn != 0)
    under = [c for c, r in out["cells"].items() if r["n"] is None or r["n"] < min_n]
    no_se = [c for c, r in out["cells"].items() if r["se"] is None]
    if out["n_cells"] < min_cells:
        out["reason"] = f"only {out['n_cells']} cells (need {min_cells})"
        return out
    if under:
        out["reason"] = (f"per-cell n per grid point {min(out['cells'][c]['n'] or 0 for c in under)} < {min_n} "
                         f"in {len(under)}/{out['n_cells']} cells: the interval carries no information")
        return out
    if no_se:
        out["reason"] = f"no bootstrap SE stored for {len(no_se)} cells (pre-2026-09-17.2 artifact)"
        return out
    out["evaluable"] = True
    out["all_exclude_zero"] = all(r["excludes_zero"] for r in out["cells"].values())
    return out


def fmt(ce, nd=0):
    if ce.get("lambda") is None:
        return f"cell_effect[{ce.get('reason')}]"
    cells = " ".join(f"{c.split('/')[0][:5]}/{c.split('/')[1][:3]}:{r['effect']:+.{nd}f}"
                     + (f"±{Z95 * r['se']:.{nd}f}" if r["se"] is not None else "") for c, r in ce["cells"].items())
    head = (f"lambda=±{ce['lambda']} median={ce['median']:+.{nd}f} median|.|={ce['median_abs']:.{nd}f} "
            f"sign_agree={ce['sign_agree']}/{ce['n_cells']} all_ci_exclude_0={ce['all_exclude_zero']}")
    return f"cell_effect[{head}" + ("" if ce["evaluable"] else f" NOT_EVALUABLE: {ce['reason']}") + f" | {cells}]"
