"""Within-group permutations and the Freedman-Lane test of one regression coefficient."""
from __future__ import annotations

import numpy as np

from llama8b_it_base_probe.stats.fe import within


def within_group_perms(groups, B, seed):
    """B permutations of all item indices, each permuting only within group; row 0 is the identity. [B + 1, n]"""
    rng = np.random.default_rng(seed)
    groups = np.asarray(groups)
    n = len(groups)
    P = np.tile(np.arange(n), (B + 1, 1))
    for c in np.unique(groups):
        idx = np.where(groups == c)[0]
        for b in range(1, B + 1):
            P[b, idx] = rng.permutation(idx)
    return P


def _p(null, obs, alternative):
    B = len(null)
    if alternative == "greater":
        k = np.sum(null >= obs - 1e-12)
    elif alternative == "less":
        k = np.sum(null <= obs + 1e-12)
    elif alternative == "two-sided":
        k = np.sum(np.abs(null) >= abs(obs) - 1e-12)
    else:
        raise ValueError(alternative)
    return float((1 + k) / (B + 1))


def freedman_lane_coef(y, Z, x, groups, B=2000, seed=0, alternative="greater"):
    """Test the coefficient of x in y ~ Z + x + group FE. Reduced model y ~ Z + FE (within transform, OLS); its
    residuals are permuted within group (B draws), added back to its fitted values, and the full model refit.
    -> (observed coefficient, p = (1 + #{null at least as extreme}) / (B + 1))."""
    g = np.asarray(groups)
    Z = np.asarray(Z, float).reshape(len(g), -1)
    yd, Zd, xd = within(y, g), within(Z, g), within(np.asarray(x, float), g)
    X = np.column_stack([Zd, xd])
    obs = float(np.linalg.lstsq(X, yd, rcond=None)[0][-1])
    c = np.linalg.lstsq(Zd, yd, rcond=None)[0]
    fit = Zd @ c
    res = yd - fit
    rng = np.random.default_rng(seed)
    idx = [np.where(g == v)[0] for v in np.unique(g)]
    pinv = np.linalg.pinv(X)
    null = np.empty(B)
    for b in range(B):
        r = res.copy()
        for ii in idx:
            r[ii] = res[rng.permutation(ii)]
        null[b] = (pinv @ (fit + r))[-1]
    return obs, _p(null, obs, alternative)
