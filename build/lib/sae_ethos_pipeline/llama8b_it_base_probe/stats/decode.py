"""Held-out decoding of a binary label from activations, over and above a baseline of other features, with a
Freedman-Lane within-group permutation p-value.

  statistic   ΔAUC = held-out AUC(activations) - held-out AUC(baseline), leave-one-group-out folds.
              activations: PCA (k) fit on the training groups, ridge (λ) on the 0/1 label;
              baseline: the given features (constant columns dropped), standardized on the training fold, same ridge.
  location    optional `loc` (a per-item scalar such as a readout's expected value): within each fold the PC scores are
              residualized on [1, loc, loc²] (training-fold OLS applied to both sides) and [loc, loc²] join the
              baseline; the location-only AUC is reported.
  test        Freedman-Lane: within each fold the activation PCs are regressed on [1, baseline] (training-fold OLS);
              the residual rows are permuted within group (one permutation of all items per draw, applied in every
              fold), the fitted values added back, and the activation decoder refit (the baseline decoder's inputs do not
              change). p = (1 + #{null ΔAUC >= observed}) / (1 + B), one-sided.
Group-centering activations first (group_center) removes group means a decoder could otherwise exploit.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import rankdata

from llama8b_it_base_probe.stats.multi import jackknife_groups
from llama8b_it_base_probe.stats.perm import within_group_perms

PCA_K = 64
RIDGE = 1.0
CHUNK = 250


def group_center(X, groups, ref_mask=None):
    """Subtract each group's mean over its ref_mask items (default: all its items) from all of its items."""
    X = np.asarray(X, dtype=np.float64).copy()
    groups = np.asarray(groups)
    ref = np.ones(len(groups), bool) if ref_mask is None else np.asarray(ref_mask, bool)
    for c in np.unique(groups):
        m = groups == c
        r = m & ref
        X[m] -= X[r].mean(0) if r.any() else X[m].mean(0)
    return X


def pca_fit(X, k=PCA_K):
    mu = X.mean(0)
    _, _, vt = np.linalg.svd(X - mu, full_matrices=False)
    return mu, vt[:min(k, vt.shape[0])]


def ridge_fit(Z, y, lam=RIDGE):
    Zm, ym = Z.mean(0), y.mean()
    Zc = Z - Zm
    w = np.linalg.solve(Zc.T @ Zc + lam * np.eye(Z.shape[1]), Zc.T @ (y - ym))
    return lambda Q: (Q - Zm) @ w + ym


def auc(score, y):
    pos, neg = score[y == 1], score[y == 0]
    if not len(pos) or not len(neg):
        return np.nan
    return float((np.sum(pos[:, None] > neg[None, :]) + 0.5 * np.sum(pos[:, None] == neg[None, :])) / (len(pos) * len(neg)))


def auc_rows(S, y):
    """AUC of every row of S (draws x items), ties counted half (= auc)."""
    r = rankdata(S, axis=1)
    n1, n0 = int(y.sum()), int((1 - y).sum())
    return (r[:, y == 1].sum(1) - n1 * (n1 + 1) / 2) / (n1 * n0)


def _loc_design(loc):
    return np.column_stack([np.ones(len(loc)), loc, loc ** 2])


def _std(Ftr, Fte):
    m, s = Ftr.mean(0), Ftr.std(0) + 1e-9
    return (Ftr - m) / s, (Fte - m) / s


def loco_scores(F, y, groups, pca, loc=None, k=PCA_K, lam=RIDGE):
    """Held-out ridge scores, leave one group out (pca: activations; else standardized features). loc: residualize on
    [1, loc, loc²] with training-fold OLS."""
    F = np.asarray(F, dtype=np.float64)
    groups = np.asarray(groups)
    out = np.full(len(y), np.nan)
    for c in np.unique(groups):
        te, tr = groups == c, groups != c
        Ftr, Fte = F[tr], F[te]
        if pca:
            mu, V = pca_fit(Ftr, k)
            Ftr, Fte = (Ftr - mu) @ V.T, (Fte - mu) @ V.T
        else:
            Ftr, Fte = _std(Ftr, Fte)
        if loc is not None:
            Ztr, Zte = _loc_design(loc[tr]), _loc_design(loc[te])
            B = np.linalg.lstsq(Ztr, Ftr, rcond=None)[0]
            Ftr, Fte = Ftr - Ztr @ B, Fte - Zte @ B
        out[te] = ridge_fit(Ftr, y[tr], lam)(Fte)
    return out


def fl_scores(X, base, y, groups, perms, loc=None, k=PCA_K, lam=RIDGE):
    """Held-out activation-decoder scores for every permutation draw (rows of perms), Freedman-Lane."""
    X = np.asarray(X, dtype=np.float64)
    groups = np.asarray(groups)
    nB, n = perms.shape
    out = np.full((nB, n), np.nan)
    for c in np.unique(groups):
        te, tr = np.where(groups == c)[0], np.where(groups != c)[0]
        mu, V = pca_fit(X[tr], k)
        Pall = (X - mu) @ V.T                                          # PCs of every item under this fold's PCA
        btr, bte = _std(base[tr], base[te])
        N = np.empty((n, base.shape[1] + 1)); N[:, 0] = 1; N[tr, 1:] = btr; N[te, 1:] = bte
        G = np.linalg.lstsq(N[tr], Pall[tr], rcond=None)[0]           # reduced model: PCs ~ baseline (training OLS)
        fit, res = N @ G, Pall - N @ G
        if loc is not None:
            Ztr, Zte = _loc_design(loc[tr]), _loc_design(loc[te])
            C = np.linalg.pinv(Ztr)                                    # location residualization, training coefficients
        ytr = y[tr]; ym = ytr.mean()
        for s in range(0, nB, CHUNK):
            Pm = perms[s:s + CHUNK]
            Xs = fit[None] + res[Pm]                                   # (b, n, k): permuted residuals + fitted
            Xtr, Xte = Xs[:, tr], Xs[:, te]
            if loc is not None:
                coef = np.einsum("in,bnk->bik", C, Xtr)
                Xtr = Xtr - np.einsum("ni,bik->bnk", Ztr, coef)
                Xte = Xte - np.einsum("ni,bik->bnk", Zte, coef)
            Zm = Xtr.mean(1, keepdims=True)
            Zc = Xtr - Zm
            A = np.einsum("bnk,bnj->bkj", Zc, Zc) + lam * np.eye(Zc.shape[2])[None]
            w = np.linalg.solve(A, np.einsum("bnk,n->bk", Zc, ytr - ym)[..., None])[..., 0]
            out[s:s + CHUNK, te] = np.einsum("bnk,bk->bn", Xte - Zm, w) + ym
    return out


def incremental_auc(X, S, y, groups, loc=None, B=2000, seed=0, perms=None, alpha=0.025, k=PCA_K, lam=RIDGE):
    """ΔAUC of activations X over baseline features S (plus [loc, loc²] when loc is given), with the Freedman-Lane
    within-group p. The jackknife CI over groups is descriptive."""
    groups = np.asarray(groups)
    S = np.asarray(S, dtype=np.float64)
    S = S[:, S.std(0) > 0] if S.size else S.reshape(len(y), 0)
    base = S if loc is None else np.column_stack([S, loc, loc ** 2])
    perms = within_group_perms(groups, B, seed) if perms is None else perms
    sb = loco_scores(base, y, groups, pca=False, k=k, lam=lam)
    sa_all = fl_scores(X, base, y, groups, perms, loc=loc, k=k, lam=lam)
    d_all = auc_rows(sa_all, y) - auc(sb, y)
    obs = float(d_all[0])
    p = float((1 + np.sum(d_all[1:] >= obs - 1e-12)) / len(d_all))
    sa = sa_all[0]
    j = lambda f: [float(v) for v in jackknife_groups(f, groups)]
    out = {"delta_auc": obs, "p_perm": p, "n_perm": len(d_all) - 1, "PASS": bool(p <= alpha), "alpha": alpha,
           "auc_activations": float(auc(sa, y)), "auc_baseline": float(auc(sb, y)),
           "baseline": "features" if loc is None else "features + [loc, loc^2]",
           "delta_auc_jackknife_ci_descriptive": j(lambda i: auc(sa[i], y[i]) - auc(sb[i], y[i])),
           "null_delta_auc_mean": float(d_all[1:].mean())}
    if loc is not None:
        out["auc_location_only"] = float(auc(loco_scores(np.column_stack([loc, loc ** 2]), y, groups, pca=False), y))
    return out
