"""Switching point of a psychometric curve. Pure numpy.

switching_point(params, labels) -> {"sp", "method", "slope", "curve"}
  primary : lapse-aware logistic P(high) = g + (1 - g - l) * sigmoid(a_z * (z - c_z)) with guess rate g and
            lapse rate l estimated from the lower/upper tails of the grid (Wichmann & Hill), the inner
            2-parameter part fit by Newton with a small ridge; sp = c mapped back to raw units (rules
            2026-09-16.3: run 2's curve plateaued at 0.88, and a fixed-asymptote fit put sp at 65.5 where the
            0.5 crossing was ~53). Rejected (fallback) if |a_z| > 50 (separable) or c lies outside the grid.
  fallback: linear interpolation of the empirical per-grid-point rate at its first 0.5 crossing.
  none    : the sweep never crosses 0.5 -> sp = None. That is a real finding (saturation), reported, never
            imputed. `method` always says which branch fired.
"""
import math
import numpy as np


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def empirical_curve(params, labels):
    """{param: rate} over trials with a non-None label, in grid order."""
    acc = {}
    for p, y in zip(params, labels):
        if y is None:
            continue
        acc.setdefault(float(p), []).append(int(y))
    return {p: float(np.mean(v)) for p, v in sorted(acc.items())}


def _tail_rates(curve, frac=0.2):
    """Guess rate g (P(high) on the LOW tail of the grid) and lapse rate l (1 - P(high) on the HIGH tail)."""
    ps = sorted(curve); k = max(1, int(round(frac * len(ps))))
    g = float(np.mean([curve[p] for p in ps[:k]]))
    l = float(1.0 - np.mean([curve[p] for p in ps[-k:]]))
    return min(max(g, 0.0), 0.45), min(max(l, 0.0), 0.45)


def _logistic_fit(x, y, ridge=1e-3, iters=100, g=0.0, l=0.0):
    """Newton on (a, b) for P = g + (1-g-l) * sigmoid(a*z + b), z standardized. Returns a_z, b_z, mu, sd."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    mu, sd = x.mean(), x.std() + 1e-9
    z = (x - mu) / sd
    w = np.zeros(2)
    X = np.stack([z, np.ones_like(z)], 1)
    scale = 1.0 - g - l
    for _ in range(iters):
        s_ = _sigmoid(X @ w)
        p = np.clip(g + scale * s_, 1e-6, 1 - 1e-6)
        # dL/dw with L = -sum y log p + (1-y) log(1-p); dp/dw = scale * s(1-s) * X
        r = (p - y) / (p * (1 - p)) * scale * s_ * (1 - s_)
        grad = X.T @ r + ridge * w
        wgt = (scale * s_ * (1 - s_)) ** 2 / (p * (1 - p))
        H = (X * wgt[:, None]).T @ X + ridge * np.eye(2)
        step = np.linalg.solve(H, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w[0], w[1], mu, sd


def _interp_crossing(curve):
    ps = list(curve); rs = [curve[p] for p in ps]
    for i in range(1, len(ps)):
        lo, hi = rs[i - 1] - 0.5, rs[i] - 0.5
        if lo == 0:
            return ps[i - 1]
        if lo * hi < 0 or hi == 0:
            t = lo / (lo - hi)
            return ps[i - 1] + t * (ps[i] - ps[i - 1])
    return None


def switching_point(params, labels):
    curve = empirical_curve(params, labels)
    out = {"sp": None, "method": "none", "slope": None, "curve": curve}
    if not curve:
        return out
    rates = np.array(list(curve.values()))
    if rates.min() > 0.5 or rates.max() < 0.5:
        return out                                            # saturation: never crosses 0.5
    x = [float(p) for p, y in zip(params, labels) if y is not None]
    y = [int(y) for y in labels if y is not None]
    g, l = _tail_rates(curve)
    out["guess_rate"], out["lapse_rate"] = g, l
    out["sp_interp"] = _interp_crossing(curve)          # always reported beside the fit
    try:
        a_z, b_z, mu, sd = _logistic_fit(x, y, g=g, l=l)
        if abs(a_z) <= 50 and a_z != 0:
            c = mu - sd * b_z / a_z                       # midpoint of the (g, 1-l) span = the 0.5 crossing of the inner sigmoid
            lo, hi = min(curve), max(curve)
            if lo <= c <= hi:
                out.update(sp=float(c), method="logistic_lapse", slope=float(a_z / sd))
                return out
    except (np.linalg.LinAlgError, FloatingPointError, ValueError):
        pass
    if out["sp_interp"] is not None:
        out.update(sp=float(out["sp_interp"]), method="interpolation")
    return out
