#!/usr/bin/env python3
"""P4: lambda sweep -> psychometric curve per lambda -> switching point; fit a monotone map lambda -> sp;
invert to hit the configured targets; re-run once at each lambda*. Injection is activation addition
h' = h + lambda * mean_resid_norm * unit(w) at EVERY position of the probe's layer, through the SAME path G4
uses (steering units = fraction_of_mean_residual_norm).

  python -m probe.calibrate --run-dir runs/<run_id> [--mock]

Writes runs/<run_id>/probe/<task>/calibration.json.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from probe.tasks import TASKS, messages, parse_choice, label, conditions   # noqa: E402
from probe.psychometric import switching_point                             # noqa: E402
from probe.synth_trials import MOCK_SP, MOCK_SLOPE, mock_sp                # noqa: E402

CFG = ROOT / "config"
MOCK_K = {"lottery": 220.0, "ultimatum": 50.0}                   # sp shift per unit lambda (mock; +-0.4 must span the targets)


def _cfg():
    return yaml.safe_load((CFG / "run.yaml").read_text())["probe"]


# ---------------------------------------------------------------- one steered sweep
def sweep_mock(task, lam, n_agents):
    rng = np.random.default_rng(int(abs(lam) * 1000) + 7)
    grid = TASKS[task]["grid"]
    sp = mock_sp(task, TASKS[task]["reference_level"]) - MOCK_K[task] * lam
    sp = float(np.clip(sp, grid[0] - 40, grid[-1] + 40))         # saturation clamp
    P, Y = [], []
    for n in grid:
        for s in range(n_agents):
            p = 1.0 / (1.0 + np.exp(-MOCK_SLOPE[task] * (n - sp)))
            P.append(n); Y.append(int(rng.random() < p))
    return switching_point(P, Y), 0


class SteeredSampler:
    """Sampled answers under steering through replay.hooks (the same injection G4 uses). Loaded once per run.
    T>0 across agents so the steered psychometric curve is graded; the same surface conditions as P1."""
    def __init__(self, run_dir, temperature, top_p):
        from replay.modelload import load_target
        self.lm = load_target("target")
        store = np.load(Path(run_dir) / "steering_vectors.npz")
        self.vecs = {k: store[k] for k in store.files}
        self.temperature, self.top_p = temperature, top_p

    def answer(self, task, n, lam, seed, level, cond, max_new_tokens=6):
        from model_io.gemma2 import apply_to_tokenizer
        from replay.hooks import sample_generate_at_layer
        vec = self.vecs[f"probe_{task}"]; layer = int(self.vecs[f"probe_{task}__layer"])
        ids = apply_to_tokenizer(self.lm.tokenizer, messages(task, n, level, cond), add_generation_prompt=True)
        out = sample_generate_at_layer(self.lm, ids, layer, (vec, lam), temperature=self.temperature,
                                       top_p=self.top_p, seed=seed * 7919 + int(abs(lam) * 1e4), max_new_tokens=max_new_tokens)
        return self.lm.tokenizer.decode(out)


def sweep_real(task, lam, n_agents, gen):
    """One steered psychometric curve at the REFERENCE level (safe 50 for the lottery)."""
    t = TASKS[task]; level = t["reference_level"]
    P, Y, dropped = [], [], 0
    for n in t["grid"]:
        for seed in range(n_agents):
            cond = conditions(task, n, seed)
            txt = gen.answer(task, n, lam, seed, level, cond)
            y = label(task, parse_choice(task, txt, cond))
            if y is None:
                dropped += 1
            P.append(n); Y.append(y)
    return switching_point(P, Y), dropped


# ---------------------------------------------------------------- monotone map + inversion
def monotone_fit(curve, tol_frac=0.10):
    """curve {lam: sp or None} -> (xs, ys, monotone_flag, direction). Pool-adjacent-violators on the
    non-None points; monotone_flag is True iff the raw points deviate from the isotonic fit by at most
    tol_frac of the curve's range (rules 2026-09-16.3: a log-spaced sweep has steps below sampling noise,
    and strict step-by-step ordering fails on wiggle; a real reversal still fails)."""
    pts = sorted((float(k), float(v)) for k, v in curve.items() if v is not None)
    if len(pts) < 2:
        return [], [], False, 0
    xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
    d = np.sign(ys[-1] - ys[0]) or 1.0
    raw_monotone = bool(np.all(np.diff(ys * d) >= -1e-9))
    # PAV isotonic regression in direction d
    z = (ys * d).copy(); w = np.ones_like(z); blocks = [[i] for i in range(len(z))]
    vals = list(z); wts = list(w)
    i = 0
    while i < len(vals) - 1:
        if vals[i] > vals[i + 1]:
            tot = wts[i] + wts[i + 1]
            vals[i] = (vals[i] * wts[i] + vals[i + 1] * wts[i + 1]) / tot; wts[i] = tot
            blocks[i] += blocks[i + 1]; del vals[i + 1], wts[i + 1], blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    fit = np.empty_like(z)
    for v, blk in zip(vals, blocks):
        fit[blk] = v
    rng_ = float(ys.max() - ys.min()) or 1.0
    monotone = bool(np.max(np.abs(z - fit)) <= tol_frac * rng_)
    return xs.tolist(), (fit * d).tolist(), monotone, int(d)


def invert(xs, ys, target):
    """Piecewise-linear inverse of the monotone map; None if target is outside the achieved range."""
    if not xs:
        return None
    lo, hi = min(ys), max(ys)
    if not (lo <= target <= hi):
        return None
    for i in range(1, len(xs)):
        a, b = ys[i - 1], ys[i]
        if (a - target) * (b - target) <= 0:
            if a == b:
                return xs[i - 1]
            t = (target - a) / (b - a)
            return xs[i - 1] + t * (xs[i] - xs[i - 1])
    return None


def calibrate_task(task, run_dir, pc, gen, mock):
    d = Path(run_dir) / "probe" / task
    base = json.loads((d / "baseline.json").read_text())
    n_agents = pc["n_agents"]
    curve, methods, dropped = {}, {}, {}
    for lam in pc["lambda_sweep"]:
        r, nd = sweep_mock(task, lam, n_agents) if mock else sweep_real(task, lam, n_agents, gen)
        curve[str(lam)] = r["sp"]; methods[str(lam)] = r["method"]; dropped[str(lam)] = nd
        print(f"  [{task}] lambda={lam:+.2f} sp={None if r['sp'] is None else round(r['sp'], 1)} ({r['method']})")
    xs, ys, monotone, direction = monotone_fit(curve)
    tr = TASKS[task].get("target_ratios")
    target_list = ([round(r * TASKS[task]["reference_level"], 1) for r in tr] if tr and TASKS[task]["reference_level"]
                   else pc["targets"][task])
    targets = {}
    for tgt in target_list:
        lam = invert(xs, ys, float(tgt))
        if lam is None:
            targets[str(tgt)] = {"lambda": None, "achieved": None, "method": "out_of_range"}
            continue
        r, _ = sweep_mock(task, lam, n_agents) if mock else sweep_real(task, lam, n_agents, gen)
        targets[str(tgt)] = {"lambda": float(lam), "achieved": r["sp"], "method": r["method"]}
    errs = [abs(v["achieved"] - float(k)) for k, v in targets.items() if v["achieved"] is not None]
    ach = [v["achieved"] for v in targets.values() if v["achieved"] is not None]
    rep = {"task": task, "baseline_sp": base["sp"], "baseline_sp_interp": base.get("sp_interp"),
           "reference_level": TASKS[task]["reference_level"], "target_list": target_list,
           "sweep_lambda0_sp": curve.get("0.0"),
           "lambda_sweep": list(pc["lambda_sweep"]), "lambda_curve": curve, "lambda_methods": methods,
           "dropped_per_lambda": dropped, "monotone": monotone, "direction": direction,
           "monotone_fit": {"lambda": xs, "sp": ys}, "targets": targets,
           "mae": float(np.mean(errs)) if errs else None,
           "range_achieved": [min(ach), max(ach)] if ach else None,
           "saturated_lambdas": [k for k, v in curve.items() if v is None], "mock": bool(mock),
           "fan2026_reference": base["fan2026_reference"]}
    (d / "calibration.json").write_text(json.dumps(rep, indent=2))
    fan = base["fan2026_reference"]
    print(f"[{task}] monotone={monotone} mae={rep['mae']} (fan:{fan['mae']}) range={rep['range_achieved']} "
          f"(fan:{fan['range']}) saturated={rep['saturated_lambdas']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    pc = _cfg()
    tasks = a.tasks.split(",") if a.tasks else pc["tasks"]
    gen = None if a.mock else SteeredSampler(a.run_dir, float(pc.get("temperature", 0.8)), float(pc.get("top_p", 0.95)))
    from probe.synth_trials import task_has_dial
    for t in tasks:
        if not task_has_dial(a.run_dir, t):
            print(f"[{t}] skipped: no dial on this model (see baseline.json)"); continue
        calibrate_task(t, a.run_dir, pc, gen, a.mock)


if __name__ == "__main__":
    main()
