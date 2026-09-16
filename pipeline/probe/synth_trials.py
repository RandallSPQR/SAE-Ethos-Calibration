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
from probe.tasks import TASKS, messages, parse_choice, label            # noqa: E402
from probe.psychometric import switching_point                          # noqa: E402

CFG = ROOT / "config"
MOCK_SP = {"lottery": 125.0, "ultimatum": 30.0}
MOCK_SLOPE = {"lottery": 0.15, "ultimatum": 0.5}


def probe_cfg():
    return yaml.safe_load((CFG / "run.yaml").read_text())["probe"]


def mock_choice(task, param, seed):
    rng = np.random.default_rng(hash((task, float(param), int(seed))) % (2 ** 32))
    p_high = 1.0 / (1.0 + math.exp(-MOCK_SLOPE[task] * (param - MOCK_SP[task])))
    hi = rng.random() < p_high
    text = TASKS[task]["options"]["high" if hi else "low"]
    ids = [hash((text, i)) % 32000 for i in range(3)]
    return {"text": text, "token_ids": ids, "token_logprobs": [-0.1] * 3}


def run_task(task, out_dir, n_agents, client, mock):
    t = TASKS[task]
    rows, dropped = [], 0
    for n in t["grid"]:
        for seed in range(n_agents):
            msgs = messages(task, n)
            if mock:
                r = mock_choice(task, n, seed)
            else:
                r = client.complete(msgs, temperature=0.0, top_p=1.0, max_tokens=16, seed=seed)
                if r.get("token_ids") is None:
                    raise RuntimeError("vLLM returned no token ids (return_token_ids); refusing to retokenize")
            choice = parse_choice(task, r["text"])
            y = label(task, choice)
            if y is None:
                dropped += 1
            rows.append({"uid": f"probe:{task}:{n}:{seed}", "task": task, "param": n, "seed": seed,
                         "text": r["text"], "token_ids": r["token_ids"], "choice": choice, "label": y,
                         "messages": msgs})
    d = Path(out_dir) / "probe" / task
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "trials.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    sp = switching_point([r["param"] for r in rows], [r["label"] for r in rows])
    base = {**sp, "task": task, "trait": t["trait"], "n_trials": len(rows), "n_dropped": dropped,
            "drop_rate": dropped / max(1, len(rows)), "n_agents": n_agents, "grid": t["grid"],
            "temperature": 0.0, "mock": bool(mock), "fan2026_reference": t["fan2026_reference"]}
    (d / "baseline.json").write_text(json.dumps(base, indent=2))
    print(f"[{task}] n={len(rows)} dropped={dropped} sp={sp['sp']} ({sp['method']})"
          f"  fan2026 baseline_sp={t['fan2026_reference']['baseline_sp']}")
    if base["drop_rate"] > 0.05:
        print(f"STOP: {task} drop rate {base['drop_rate']:.2%} > 5% — the model isn't answering the format; "
              "fix the prompt at T0, not on the meter.")
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
    ok = all([run_task(t, a.run_dir, n_agents, client, a.mock) for t in tasks])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
