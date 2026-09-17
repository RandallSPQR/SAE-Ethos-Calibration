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
    def __init__(self, run_dir, temperature, top_p, batch_size=32):
        from replay.modelload import load_target
        self.lm = load_target("target")
        store = np.load(Path(run_dir) / "steering_vectors.npz")
        self.vecs = {k: store[k] for k in store.files}
        self.temperature, self.top_p = temperature, top_p
        self.batch_size = int(batch_size)
        self.batched_ok = {}                      # task -> batch gate passed (probe.batch_gate)

    def allow_batch(self, task, run_dir):
        p = Path(run_dir) / "probe" / task / "batch_gate.json"
        ok = p.exists() and json.loads(p.read_text()).get("ok") is True
        self.batched_ok[task] = ok
        print(f"[{task}] batched sampling {'ENABLED (batch gate passed)' if ok else 'DISABLED (no passing batch_gate.json)'}")
        return ok

    def answers_batch(self, task, items, lam, max_new_tokens=6):
        """items: [(n, seed, level, cond)] -> texts, through the batched path in chunks."""
        from model_io.gemma2 import apply_to_tokenizer
        from replay.hooks import sample_generate_batch_at_layer
        vec = self.vecs[f"probe_{task}"]; layer = int(self.vecs[f"probe_{task}__layer"])
        texts = []
        for i in range(0, len(items), self.batch_size):
            chunk = items[i:i + self.batch_size]
            ids = [apply_to_tokenizer(self.lm.tokenizer, messages(task, n, level, cond), add_generation_prompt=True)
                   for n, seed, level, cond in chunk]
            seeds = [seed * 7919 + int(abs(lam) * 1e4) for n, seed, level, cond in chunk]
            outs = sample_generate_batch_at_layer(self.lm, ids, layer, (vec, lam), temperature=self.temperature,
                                                  top_p=self.top_p, seeds=seeds, max_new_tokens=max_new_tokens)
            texts += [self.lm.tokenizer.decode(o) for o in outs]
        return texts

    def answer(self, task, n, lam, seed, level, cond, max_new_tokens=6):
        from model_io.gemma2 import apply_to_tokenizer
        from replay.hooks import sample_generate_at_layer
        vec = self.vecs[f"probe_{task}"]; layer = int(self.vecs[f"probe_{task}__layer"])
        ids = apply_to_tokenizer(self.lm.tokenizer, messages(task, n, level, cond), add_generation_prompt=True)
        out = sample_generate_at_layer(self.lm, ids, layer, (vec, lam), temperature=self.temperature,
                                       top_p=self.top_p, seed=seed * 7919 + int(abs(lam) * 1e4), max_new_tokens=max_new_tokens)
        return self.lm.tokenizer.decode(out)


def sweep_real(task, lam, n_agents, gen, seed_offset=0):
    """One steered psychometric curve at the REFERENCE level (safe 50 for the lottery). Batched across grid
    points and agents when the batch gate passed; otherwise one sampled answer at a time."""
    t = TASKS[task]; level = t["reference_level"]
    items = [(n, seed_offset + seed, level, conditions(task, n, seed_offset + seed)) for n in t["grid"] for seed in range(n_agents)]
    if gen.batched_ok.get(task):
        texts = gen.answers_batch(task, items, lam)
    else:
        texts = [gen.answer(task, n, lam, seed, level, cond) for n, seed, level, cond in items]
    P, Y, dropped = [], [], 0
    for (n, seed, level, cond), txt in zip(items, texts):
        y = label(task, parse_choice(task, txt, cond))
        if y is None:
            dropped += 1
        P.append(n); Y.append(y)
    return switching_point(P, Y), dropped


# ---------------------------------------------------------------- lambda=0 checksum (served vs steered sampler)
def _bootstrap_sp(params, labels, n_boot=200, seed=0):
    """Bootstrap SE of the switching point over trials (resampling within grid point keeps the design)."""
    rng = np.random.default_rng(seed)
    params = np.asarray(params); labels = np.asarray(labels, dtype=object)
    sps = []
    for _ in range(n_boot):
        idx = np.concatenate([rng.choice(np.where(params == v)[0], size=(params == v).sum(), replace=True)
                              for v in np.unique(params)])
        r = switching_point(params[idx].tolist(), labels[idx].tolist())
        if r["sp"] is not None:
            sps.append(r["sp"])
    return (float(np.std(sps)) if len(sps) > 1 else None), len(sps)


def _bootstrap_from_curve(curve, n_per_point, n_boot=200, seed=0):
    """Bootstrap SE of the switching point from per-grid-point rates with n_per_point trials each."""
    rng = np.random.default_rng(seed)
    ps = sorted(curve, key=float); sps = []
    for _ in range(n_boot):
        P, Y = [], []
        for p in ps:
            k = rng.binomial(n_per_point, curve[p])
            P += [float(p)] * n_per_point; Y += [1] * k + [0] * (n_per_point - k)
        r = switching_point(P, Y)
        if r["sp"] is not None:
            sps.append(r["sp"])
    return float(np.std(sps)) if len(sps) > 1 else None


def lambda0_checksum(task, run_dir, pc, gen, mock):
    """The steered sampler at lambda=0 must reproduce the SERVED model's unsteered curve at the reference
    level: |sp_sampler - sp_served| <= tol_se * sqrt(SE_served^2 + SE_sampler^2), with SEs from a within-grid
    bootstrap. Served side: the P1 trials (vLLM, T=0.8). Sampler side: `lambda0_seeds` fresh seeds here.
    Fails loudly (STOP) if the gap exceeds the tolerance: the sampler would then be a different instrument
    from the served model and nothing downstream may ride on it. Writes probe/<task>/lambda0_checksum.json."""
    t = TASKS[task]; level = t["reference_level"]
    d = Path(run_dir) / "probe" / task
    served = [json.loads(l) for l in open(d / "trials.jsonl") if l.strip()]
    served = [r for r in served if r.get("level") == level and r["label"] is not None]
    sp_served = switching_point([r["param"] for r in served], [r["label"] for r in served])
    se_served, _ = _bootstrap_sp([r["param"] for r in served], [r["label"] for r in served])
    n_seeds = int(pc.get("lambda0_seeds", 32))
    if mock:
        from probe.synth_trials import mock_choice
        P, Y = [], []
        for n in t["grid"]:
            for seed in range(n_seeds):
                cond = conditions(task, n, seed)
                P.append(n); Y.append(label(task, parse_choice(task, mock_choice(task, n, 1000 + seed, level)["text"], cond)))
        sp_s = switching_point(P, Y)
    else:
        sp_s, _ = sweep_real(task, 0.0, n_seeds, gen, seed_offset=1000)
        P = [n for n in t["grid"] for _ in range(n_seeds)]
        Y = [1 if v > 0.5 else 0 for v in []]      # placeholder; SE below uses the curve's own bootstrap
    if mock:
        se_s, _ = _bootstrap_sp(P, Y)
    else:
        se_s = _bootstrap_from_curve(sp_s["curve"], n_seeds)
    gap = None if (sp_s["sp"] is None or sp_served["sp"] is None) else abs(sp_s["sp"] - sp_served["sp"])
    se_tot = None if (se_served is None or se_s is None) else float(np.sqrt(se_served ** 2 + se_s ** 2))
    tol = float(pc.get("lambda0_tol_se", 2.0))
    ok = gap is not None and se_tot is not None and gap <= tol * se_tot
    rep = {"task": task, "level": level, "served": {"sp": sp_served["sp"], "method": sp_served["method"], "se": se_served, "n": len(served)},
           "sampler": {"sp": sp_s["sp"], "method": sp_s["method"], "se": se_s, "n": len(P), "seeds": n_seeds},
           "gap": gap, "se_combined": se_tot, "tol_se": tol, "ok": ok}
    (d / "lambda0_checksum.json").write_text(json.dumps(rep, indent=2))
    print(f"[{task}] lambda=0 checksum: served sp={None if sp_served['sp'] is None else round(sp_served['sp'], 1)}"
          f"+-{None if se_served is None else round(se_served, 1)}  sampler sp={None if sp_s['sp'] is None else round(sp_s['sp'], 1)}"
          f"+-{None if se_s is None else round(se_s, 1)}  gap={None if gap is None else round(gap, 1)}  "
          f"tol={tol}xSE={None if se_tot is None else round(tol * se_tot, 1)}  -> {'OK' if ok else 'STOP'}")
    return ok, rep


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
    ok0, chk = lambda0_checksum(task, run_dir, pc, gen, mock)
    if not ok0:
        print(f"STOP: {task} steered sampler does not reproduce the served model at lambda=0 "
              f"(gap {chk['gap']} > {chk['tol_se']} x SE {chk['se_combined']}); the sampler is a different instrument.")
        (d / "calibration.json").write_text(json.dumps({"task": task, "error": "lambda0_checksum_failed", **chk}, indent=2))
        return
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
           "lambda0_checksum": chk,
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
    gen = None if a.mock else SteeredSampler(a.run_dir, float(pc.get("temperature", 0.8)), float(pc.get("top_p", 0.95)),
                                             batch_size=int(pc.get("batch_size", 32)))
    from probe.synth_trials import task_has_dial
    for t in tasks:
        if not task_has_dial(a.run_dir, t):
            print(f"[{t}] skipped: no dial on this model (see baseline.json)"); continue
        if gen is not None:
            gen.allow_batch(t, a.run_dir)
        calibrate_task(t, a.run_dir, pc, gen, a.mock)


if __name__ == "__main__":
    main()
