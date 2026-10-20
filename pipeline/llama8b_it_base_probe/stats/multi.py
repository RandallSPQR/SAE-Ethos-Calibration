"""Holm adjustment and the leave-one-group-out jackknife."""
from __future__ import annotations

import numpy as np
from scipy.stats import t as student_t


def holm(pvals):
    """Holm step-down adjusted p-values (dict in, dict out)."""
    ks = sorted(pvals, key=pvals.get)
    m, run, out = len(ks), 0.0, {}
    for i, k in enumerate(ks):
        run = max(run, min(1.0, (m - i) * pvals[k]))
        out[k] = run
    return out


def jackknife_groups(stat, groups, level=0.95):
    """Leave-one-group-out jackknife: point = stat on all items; SE = sqrt((n-1)/n * sum (theta_(i) - mean)^2) over the
    n leave-one-group-out estimates; CI = point -/+ t(level, n-1) * SE. stat(index array) -> scalar or vector."""
    groups = np.asarray(groups)
    cs = np.unique(groups)
    n = len(cs)
    point = np.asarray(stat(np.arange(len(groups))), dtype=np.float64)
    loo = np.asarray([stat(np.where(groups != c)[0]) for c in cs], dtype=np.float64)
    se = np.sqrt((n - 1) / n * np.nansum((loo - np.nanmean(loo, axis=0)) ** 2, axis=0))
    t = float(student_t.ppf(0.5 + level / 2, n - 1))
    pt = point if point.ndim else float(point)
    return pt, point - t * se, point + t * se
