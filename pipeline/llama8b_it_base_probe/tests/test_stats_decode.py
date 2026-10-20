"""incremental_auc on synthetic data (ported from the trust study's p2/test_q1_v2.py; B = 300 here for speed): a planted
label direction passes with and without location adjustment; a null passes neither; a location-only effect passes
unadjusted but not adjusted; a surface-only effect passes neither; the identity draw equals the plain decoder; Holm;
group centering; the jackknife."""
import numpy as np
import pytest

from llama8b_it_base_probe.stats import decode as D
from llama8b_it_base_probe.stats.multi import holm, jackknife_groups
from llama8b_it_base_probe.stats.perm import within_group_perms

N_G, PER = 24, 2            # 24 groups x (2 positive + 2 negative items)
DIM, NS = 80, 8
B = 300


def data(seed, effect=0.0, loc_effect=0.0, x_loc=0.0, surf_effect=0.0, x_surf=0.0):
    rng = np.random.default_rng(seed)
    g = np.repeat([f"c{i:02d}" for i in range(N_G)], 2 * PER)
    y = np.tile(np.r_[np.ones(PER), np.zeros(PER)], N_G)
    w, v, u = (rng.normal(size=DIM) for _ in range(3))
    w, v, u = (a / np.linalg.norm(a) for a in (w, v, u))
    loc = 4 + loc_effect * y + rng.normal(0, 0.3, len(y))
    S = rng.normal(size=(len(y), NS))
    S[:, 0] += surf_effect * y
    X = rng.normal(size=(len(y), DIM))
    X += 3.0 * effect * np.outer(y - 0.5, w) + x_loc * np.outer(loc - 4, v) + x_surf * np.outer(S[:, 0], u)
    return X, S, y, g, loc


def both(X, S, y, g, loc, seed=0):
    P = within_group_perms(g, B, seed)
    return D.incremental_auc(X, S, y, g, perms=P), D.incremental_auc(X, S, y, g, loc=loc, perms=P)


def test_planted_effect_passes_both():
    u, a = both(*data(0, effect=1.0))
    assert u["PASS"] and a["PASS"]
    assert u["delta_auc"] > 0.3 and u["p_perm"] <= 1 / (B + 1) + 1e-12 and u["n_perm"] == B


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_null_passes_neither(seed):
    u, a = both(*data(seed))
    assert not u["PASS"] and not a["PASS"]


def test_identity_draw_is_the_plain_decoder():
    X, S, y, g, loc = data(0, effect=1.0)
    P = within_group_perms(g, 3, 0)
    assert (P[0] == np.arange(len(y))).all()
    for c in np.unique(g):                                             # every draw permutes within group only
        m = g == c
        assert all(set(P[b, m]) == set(np.where(m)[0]) for b in range(4))
    base = np.column_stack([S, loc, loc ** 2])
    assert np.allclose(D.fl_scores(X, S, y, g, P)[0], D.loco_scores(X, y, g, True))
    assert np.allclose(D.fl_scores(X, base, y, g, P, loc=loc)[0], D.loco_scores(X, y, g, True, loc=loc))


def test_location_only_effect_is_removed_by_adjustment():
    u, a = both(*data(4, loc_effect=0.8, x_loc=6.0), seed=4)
    assert u["PASS"] and not a["PASS"]
    assert a["auc_location_only"] > 0.8


def test_surface_only_effect_passes_neither():
    u, a = both(*data(5, surf_effect=2.0, x_surf=3.0))
    assert u["auc_baseline"] > 0.8
    assert not u["PASS"] and not a["PASS"]


def test_holm():
    assert holm({"a": 0.01, "b": 0.04, "c": 0.03}) == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})
    assert holm({"a": 0.6, "b": 0.7}) == pytest.approx({"a": 1.0, "b": 1.0})


def test_auc_rows_equals_auc_and_group_center():
    rng = np.random.default_rng(0)
    y = np.r_[np.ones(7), np.zeros(9)]
    S = rng.integers(0, 4, size=(5, 16)).astype(float)                 # ties included
    assert np.allclose(D.auc_rows(S, y), [D.auc(s, y) for s in S])
    X = rng.normal(size=(6, 3)); g = np.array(list("aabbcc")); ref = np.array([1, 0, 1, 1, 0, 1], bool)
    Xc = D.group_center(X, g, ref)
    assert np.allclose(Xc[0], 0) and np.allclose(Xc[2:4].mean(0), 0)
    assert np.allclose(D.group_center(X, g)[4:].mean(0), 0)


def test_jackknife_groups_on_a_mean():
    g = np.repeat(np.arange(10), 3)
    x = np.arange(30, dtype=float)
    pt, lo, hi = jackknife_groups(lambda i: x[i].mean(), g)
    assert pt == pytest.approx(x.mean()) and lo < pt < hi
    from scipy.stats import t
    gm = x.reshape(10, 3).mean(1)                                      # equal groups: jackknife SE = SE of group means
    assert hi - pt == pytest.approx(t.ppf(0.975, 9) * gm.std(ddof=1) / np.sqrt(10))
