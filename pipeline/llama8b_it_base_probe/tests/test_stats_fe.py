"""FE OLS with CR2, Freedman-Lane on one coefficient, and 2SLS on synthetic data shaped like the trust A32 pilot
(6 groups x 104 rows; two correlated predictors, r ~ 0.37; two noisy raters) (ported from p2/pilot_v3/test_regression.py,
without the study's decision rule and 6-cell summary): OLS recovers planted coefficients and its CR2 t(G-1) CIs cover
them; the permutation test is quiet under a null and fires under an effect; IV with the other rater as instrument undoes
the attenuation from rating noise."""
import numpy as np
import pytest

from llama8b_it_base_probe.stats.fe import iv_2sls, ols_fe, within
from llama8b_it_base_probe.stats.perm import freedman_lane_coef

G, N = 6, 104


def rows(seed, bt=0.3, be=0.2, rating_sd=0.0, y_sd=0.4):
    rng = np.random.default_rng(seed)
    out = {k: [] for k in ("g", "y", "t", "e", "t1", "t2", "e1", "e2")}
    for c in range(G):
        fe = rng.normal(0, 0.5)
        T = rng.normal(0, 1.5, N)
        E = 0.37 * T + np.sqrt(1 - 0.37 ** 2) * rng.normal(0, 1.5, N)
        y = 4 + fe + bt * T + be * E + rng.normal(0, y_sd, N)
        for i in range(N):
            t1, t2 = T[i] + rng.normal(0, rating_sd), T[i] + rng.normal(0, rating_sd)
            e1, e2 = E[i] + rng.normal(0, rating_sd), E[i] + rng.normal(0, rating_sd)
            for k, v in zip(out, (f"c{c}", y[i], (t1 + t2) / 2, (e1 + e2) / 2, t1, t2, e1, e2)):
                out[k].append(v)
    return {k: np.array(v) for k, v in out.items()}


def test_ols_recovers_and_ci_covers():
    cover = 0
    for s in range(20):
        d = rows(s)
        b, se, ci = ols_fe(d["y"], np.column_stack([d["t"], d["e"]]), d["g"])
        cover += ci[1][0] <= 0.2 <= ci[1][1]
    d = rows(0)
    b, se, ci = ols_fe(d["y"], np.column_stack([d["t"], d["e"]]), d["g"])
    assert abs(b[0] - 0.3) < 0.05 and abs(b[1] - 0.2) < 0.05
    r = np.corrcoef(within(d["t"], d["g"]), within(d["e"], d["g"]))[0, 1]
    assert 0.25 < abs(r) < 0.5
    assert cover >= 17                                                  # CR2 + t(5): near-nominal over 20 seeds


def test_permutation_null_and_power():
    d = rows(1, be=0.0)
    assert freedman_lane_coef(d["y"], d["t"], d["e"], d["g"], B=300)[1] > 0.025
    d = rows(2, be=0.2)
    b, p = freedman_lane_coef(d["y"], d["t"], d["e"], d["g"], B=300)
    assert p <= 0.01 and b == pytest.approx(ols_fe(d["y"], np.column_stack([d["t"], d["e"]]), d["g"])[0][1])
    assert freedman_lane_coef(d["y"], d["t"], d["e"], d["g"], B=300, alternative="less")[1] > 0.5
    assert freedman_lane_coef(d["y"], d["t"], d["e"], d["g"], B=300, alternative="two-sided")[1] <= 0.01


def test_iv_undoes_attenuation():
    d = rows(3, rating_sd=1.2)
    ols = ols_fe(d["y"], np.column_stack([d["t"], d["e"]]), d["g"])[0][1]
    ivs = []
    for end, ins in (((d["t2"], d["e2"]), (d["t1"], d["e1"])), ((d["t1"], d["e1"]), (d["t2"], d["e2"]))):
        b, se, ci, fs = iv_2sls(d["y"], np.column_stack(end), np.column_stack(ins), d["g"])
        ivs.append(b[1])
        assert all(f > 0.5 for f in fs)
    assert ols < 0.17                                                   # attenuated towards 0
    assert all(abs(b - 0.2) < abs(ols - 0.2) for b in ivs)
