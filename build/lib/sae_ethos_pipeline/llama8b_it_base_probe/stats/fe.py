"""Group fixed-effects OLS with CR2 cluster-robust SEs, and 2SLS with cluster-robust SEs; CIs use t(G - 1)."""
from __future__ import annotations

import numpy as np
from scipy.stats import t as student_t


def within(A, g):
    """Subtract each group's mean (the fixed-effects within transform)."""
    A = np.asarray(A, float).copy()
    g = np.asarray(g)
    for v in np.unique(g):
        m = g == v
        A[m] -= A[m].mean(0)
    return A


def cr2(X, e, g):
    """CR2 (Bell-McCaffrey) cluster-robust covariance for OLS on within-transformed X."""
    g = np.asarray(g)
    XtXi = np.linalg.inv(X.T @ X)
    meat = np.zeros((X.shape[1], X.shape[1]))
    for v in np.unique(g):
        m = g == v
        Xg = X[m]
        H = Xg @ XtXi @ Xg.T
        w, U = np.linalg.eigh(np.eye(m.sum()) - H)
        A = U @ np.diag(1 / np.sqrt(np.clip(w, 1e-12, None))) @ U.T
        s = Xg.T @ A @ e[m]
        meat += np.outer(s, s)
    return XtXi @ meat @ XtXi


def ols_fe(y, X, g, level=0.95):
    """y ~ X + group FE. -> (b, se, [(lo, hi)] with t(G - 1) quantiles)."""
    g = np.asarray(g)
    X = np.asarray(X, float).reshape(len(g), -1)
    yd, Xd = within(y, g), within(X, g)
    b = np.linalg.lstsq(Xd, yd, rcond=None)[0]
    V = cr2(Xd, yd - Xd @ b, g)
    G = len(np.unique(g))
    q = float(student_t.ppf(0.5 + level / 2, G - 1))
    se = np.sqrt(np.diag(V))
    return b, se, [(float(bi - q * s), float(bi + q * s)) for bi, s in zip(b, se)]


def iv_2sls(y, Xend, Z, g, level=0.95):
    """2SLS with all regressors endogenous (instruments Z, same dimension), group FE; CR1 cluster-robust SEs.
    -> (b, se, ci, first-stage r per regressor). Typical use: one rater's scores instrumented by another rater's."""
    g = np.asarray(g)
    yd, Xd, Zd = within(y, g), within(Xend, g), within(Z, g)
    Pz = Zd @ np.linalg.pinv(Zd.T @ Zd) @ Zd.T
    Xh = Pz @ Xd
    b = np.linalg.solve(Xh.T @ Xd, Xh.T @ yd)
    e = yd - Xd @ b
    Ai = np.linalg.inv(Xh.T @ Xd)
    meat = np.zeros((Xd.shape[1],) * 2)
    for v in np.unique(g):
        m = g == v
        s = Xh[m].T @ e[m]
        meat += np.outer(s, s)
    G, n, k = len(np.unique(g)), len(y), Xd.shape[1]
    V = Ai @ meat @ Ai.T * (G / (G - 1)) * ((n - 1) / (n - k))
    se = np.sqrt(np.diag(V))
    q = float(student_t.ppf(0.5 + level / 2, G - 1))
    first_stage_r = [float(np.corrcoef(Xd[:, j], Zd[:, j])[0, 1]) for j in range(k)]
    return b, se, [(float(bi - q * s), float(bi + q * s)) for bi, s in zip(b, se)], first_stage_r
