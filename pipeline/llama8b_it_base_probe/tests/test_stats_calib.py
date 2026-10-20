"""Null calibration helpers: on design-shaped nulls the Freedman-Lane coefficient test's false-pass rate has a CI that
covers alpha; power rises with the effect; mde interpolates (the trust study's q1_validation pattern, small here)."""
import numpy as np
import pytest

from llama8b_it_base_probe.stats import calib
from llama8b_it_base_probe.stats.perm import freedman_lane_coef


def gen(effect):
    def make(seed):
        rng = np.random.default_rng(seed)
        g = np.repeat(np.arange(6), 30)
        z = rng.normal(size=len(g)); x = 0.4 * z + rng.normal(size=len(g))
        y = rng.normal(size=6)[g] + 0.5 * z + effect * x + rng.normal(size=len(g))
        return y, z, x, g
    return make


def fl_test(d):
    y, z, x, g = d
    b, p = freedman_lane_coef(y, z, x, g, B=199, seed=1)
    return {"PASS": p <= 0.05, "b": b}


def test_null_false_pass_rate_covers_alpha():
    rs = calib.simulate(fl_test, gen(0.0), range(60))
    r = calib.pass_rate([x["PASS"] for x in rs], alpha=0.05)
    assert r["n"] == 60 and r["ci_covers_alpha"], r


def test_power_rises_and_mde():
    effects = (0.05, 0.15, 0.3)
    power = [np.mean([x["PASS"] for x in calib.simulate(fl_test, gen(e), range(100, 130))]) for e in effects]
    assert power[0] < power[-1] and power[-1] >= 0.9
    assert calib.mde([0.1, 0.2, 0.3], [0.2, 0.6, 1.0]) == pytest.approx(0.25)
    assert calib.mde([0.1, 0.2], [0.1, 0.2]) is None
