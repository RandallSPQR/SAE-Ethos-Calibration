"""Switching point of a psychometric curve. Pure numpy.

switching_point(params, labels) -> {"sp", "method", "slope", "curve"}
  primary : 2-parameter logistic P(high) = sigmoid(a_z * (z - c_z)) on standardized x, fit by Newton with a
            small ridge; sp = c mapped back to raw units. Rejected (fallback) if |a_z| > 50 (separable) or c
            lies outside the grid.
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


def _logistic_fit(x, y, ridge=1e-3, iters=100):
    """Newton on (a, b) for P = sigmoid(a*z + b), z standardized. Returns a_z, b_z, mu, sd."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    mu, sd = x.mean(), x.std() + 1e-9
    z = (x - mu) / sd
    w = np.zeros(2)
    X = np.stack([z, np.ones_like(z)], 1)
    for _ in range(iters):
        p = _sigmoid(X @ w)
        g = X.T @ (p - y) + ridge * w
        H = (X * (p * (1 - p))[:, None]).T @ X + ridge * np.eye(2)
        step = np.linalg.solve(H, g)
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
    try:
        a_z, b_z, mu, sd = _logistic_fit(x, y)
        if abs(a_z) <= 50 and a_z != 0:
            c = mu - sd * b_z / a_z
            lo, hi = min(curve), max(curve)
            if lo <= c <= hi:
                out.update(sp=float(c), method="logistic", slope=float(a_z / sd))
                return out
    except (np.linalg.LinAlgError, FloatingPointError, ValueError):
        pass
    sp = _interp_crossing(curve)
    if sp is not None:
        out.update(sp=float(sp), method="interpolation")
    return out
