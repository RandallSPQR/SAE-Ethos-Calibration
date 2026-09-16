#!/usr/bin/env python3
"""P1: parameter sweep through the vLLM target at T=0; label by OBSERVED choice.

  python -m probe.synth_trials --run-dir runs/<run_id> [--mock] [--tasks lottery,ultimatum]

Writes runs/<run_id>/probe/<task>/trials.jsonl (one row per trial, uid = probe:<task>:<param>:<seed>) and
baseline.json (switching_point + n_trials + n_dropped + Fan et al. reference). Raw vLLM token ids are
REQUIRED (same rule as G1); missing ids is a hard failure. STOP if > 5% of trials are unparsed.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from probe.tasks import TASKS, messages, parse_choice, label, conditions  # noqa: E402
from probe.psychometric import switching_point                          # noqa: E402

CFG = ROOT / "config"
MOCK_SP = {"lottery": 125.0, "ultimatum": 30.0}      # lottery: at the reference level (safe 50); scales with safe/50
MOCK_SLOPE = {"lottery": 0.15, "ultimatum": 0.5}


def mock_sp(task, level):
    if task == "lottery" and level is not None:
        return MOCK_SP[task] * level / TASKS[task]["reference_level"]
    return MOCK_SP[task]


def probe_cfg():
    return yaml.safe_load((CFG / "run.yaml").read_text())["probe"]


def mock_choice(task, param, seed, level=None):
    rng = np.random.default_rng(hash((task, float(param), int(seed), level)) % (2 ** 32))
    p_high = 1.0 / (1.0 + math.exp(-MOCK_SLOPE[task] * (param - mock_sp(task, level))))
    hi = rng.random() < p_high
    text = TASKS[task]["options"]["high" if hi else "low"]
    ids = [hash((text, i)) % 32000 for i in range(3)]
    return {"text": text, "token_ids": ids, "token_logprobs": [-0.1] * 3}


def run_task(task, out_dir, n_agents, client, mock, temperature=0.8, top_p=0.95):
    t = TASKS[task]
    rows, dropped = [], 0
    for level in t["levels"]:
      for n in t["grid"]:
        for seed in range(n_agents):
            cond = conditions(task, n, seed)
            msgs = messages(task, n, level, cond)
            if mock:
                r = mock_choice(task, n, seed, level)
            else:
                # T>0 across agents is what gives a GRADED psychometric curve (Fan et al. sampled). At T=0 a
                # deterministic model is a step function and the seed axis is degenerate (run 1: 2 distinct
                # responses in 280 trials). The temperature is recorded per trial.
                r = client.complete(msgs, temperature=temperature, top_p=top_p, max_tokens=16, seed=seed)
                if r.get("token_ids") is None:
                    raise RuntimeError("vLLM returned no token ids (return_token_ids); refusing to retokenize")
            choice = parse_choice(task, r["text"], cond)
            y = label(task, choice)
            if y is None:
                dropped += 1
            rows.append({"uid": f"probe:{task}:{level}:{n}:{seed}", "task": task, "param": n, "level": level,
                         "seed": seed, "cond": cond, "sampling": {"temperature": temperature, "top_p": top_p},
                         "text": r["text"], "token_ids": r["token_ids"], "choice": choice, "label": y,
                         "messages": msgs})
    d = Path(out_dir) / "probe" / task
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "trials.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    ref = t["reference_level"]
    ref_rows = [r for r in rows if r["level"] == ref]
    sp = switching_point([r["param"] for r in ref_rows], [r["label"] for r in ref_rows])
    sp_by_level = {str(lv): switching_point([r["param"] for r in rows if r["level"] == lv],
                                            [r["label"] for r in rows if r["level"] == lv]) for lv in t["levels"]}
    base = {**sp, "task": task, "trait": t["trait"], "reference_level": ref, "levels": t["levels"],
            "heldout_level": t["heldout_level"], "decision_variable": t["decision_variable"],
            "sp_by_level": {k: {"sp": v["sp"], "method": v["method"], "sp_over_level": (None if v["sp"] is None or k == "None" else v["sp"] / float(k))}
                            for k, v in sp_by_level.items()},
            "n_trials": len(rows), "n_dropped": dropped,
            "drop_rate": dropped / max(1, len(rows)), "n_agents": n_agents, "grid": t["grid"],
            "temperature": temperature, "top_p": top_p, "mock": bool(mock),
            "n_distinct_responses": len(set(r["text"] for r in rows)),
            "n_graded_grid_points": sum(1 for v in sp["curve"].values() if 0.0 < v < 1.0),
            "fan2026_reference": t["fan2026_reference"]}
    (d / "baseline.json").write_text(json.dumps(base, indent=2))
    print(f"[{task}] n={len(rows)} dropped={dropped} sp@ref={None if sp['sp'] is None else round(sp['sp'], 1)} ({sp['method']})"
          f"  by level: " + " ".join(f"{k}:{None if v['sp'] is None else round(v['sp'], 1)}" for k, v in base["sp_by_level"].items())
          + f"  fan2026 baseline_sp={t['fan2026_reference']['baseline_sp']}")
    if base["drop_rate"] > 0.05:
        print(f"STOP: {task} drop rate {base['drop_rate']:.2%} > 5% — the model isn't answering the format; "
              "fix the prompt at T0, not on the meter.")
        return False
    if sp["sp"] is None:
        print(f"STOP: {task} unsteered curve never crosses 0.5 (saturated) — the grid is mis-scaled for this model; "
              "retune the grid at T0.")
        return False
    if base["n_graded_grid_points"] < 2 and not mock:
        print(f"STOP: {task} curve is a hard step ({base['n_graded_grid_points']} graded grid points) — no "
              "within-grid label diversity; raise probe.temperature / n_agents so the psychometric curve is graded.")
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--n-agents", type=int, default=None)
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    pc = probe_cfg()
    tasks = a.tasks.split(",") if a.tasks else pc["tasks"]
    n_agents = a.n_agents or pc["n_agents"]
    client = None
    if not a.mock:
        from resample.target_client import TargetClient
        client = TargetClient(mock=False)
    ok = all([run_task(t, a.run_dir, n_agents, client, a.mock, temperature=float(pc.get("temperature", 0.8)),
                       top_p=float(pc.get("top_p", 0.95))) for t in tasks])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
