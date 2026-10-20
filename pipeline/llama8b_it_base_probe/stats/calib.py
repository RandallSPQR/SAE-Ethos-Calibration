"""Calibrating a test on design-shaped synthetic data before any real number exists.

  simulate(test, gen, seeds, workers)   run test(gen(seed)) over seeds -> list of result dicts (each must carry "PASS")
  pass_rate(passes, alpha)              false-pass count and rate with its exact (Clopper-Pearson) 95 % CI; the test
                                        is calibrated on these nulls if the CI covers alpha
  mde(stats, power, target)             the smallest mean statistic reaching `target` power, by linear interpolation of
                                        power against the mean observed statistic (None if the grid never crosses it)
"""
from __future__ import annotations

from multiprocessing import Pool

import numpy as np
from scipy.stats import binomtest


def _one(job):
    test, gen, seed = job
    return test(gen(seed))


def simulate(test, gen, seeds, workers=1):
    jobs = [(test, gen, s) for s in seeds]
    if workers <= 1:
        return [_one(j) for j in jobs]
    with Pool(workers) as pool:
        return pool.map(_one, jobs, chunksize=2)


def pass_rate(passes, alpha=0.025):
    k, n = int(sum(bool(x) for x in passes)), len(passes)
    lo, hi = binomtest(k, n).proportion_ci()
    return {"passes": k, "n": n, "rate": k / n, "rate_95ci": [float(lo), float(hi)], "alpha": alpha,
            "ci_covers_alpha": bool(lo <= alpha <= hi)}


def mde(stats, power, target=0.8):
    d, pr = np.asarray(stats, float), np.asarray(power, float)
    o = np.argsort(d); d, pr = d[o], pr[o]
    if pr.max() < target or pr.min() >= target:
        return None
    i = int(np.argmax(pr >= target))
    return float(d[i - 1] + (target - pr[i - 1]) * (d[i] - d[i - 1]) / (pr[i] - pr[i - 1])) if i else float(d[0])
