#!/usr/bin/env python3
"""Stage 1: run Petri over the built seeds. Thin wrapper around `inspect eval inspect_petri/audit`.

This wrapper's job is (a) build the command from config/models.yaml, (b) enforce the cost ceiling via
the ledger BEFORE launching, (c) tag the run so extract_transcripts can find the .eval logs.

# STUB: the actual `inspect eval` subprocess is gated behind --go so this never spends money by accident
and so the scaffold runs without Petri/Inspect installed. Fill by removing the guard once a box + API
keys are in place.
"""
import argparse
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CFG = HERE.parent / "config"


def load_cfg():
    models = yaml.safe_load((CFG / "models.yaml").read_text())
    run = yaml.safe_load((CFG / "run.yaml").read_text())
    return models, run


def ledger_total(path):
    import json
    if not Path(path).exists():
        return 0.0
    return sum(json.loads(l).get("cost_usd", 0) for l in open(path) if l.strip())


def build_cmd(seeds_dir, models, run, log_dir):
    r = models["roles"]
    return [
        "inspect", "eval", "inspect_petri/audit",
        "-T", f"seed_instructions={seeds_dir}",
        "-T", f"max_turns={run['petri']['max_turns']}",
        "-T", f"enable_rollback={str(run['petri']['enable_rollback']).lower()}",
        "--model-role", f"auditor={r['auditor']}",
        "--model-role", f"target={r['target']}",
        "--model-role", f"judge={r['judge']}",
        "--log-dir", str(log_dir),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--log-dir", default="logs")
    ap.add_argument("--go", action="store_true", help="actually launch inspect eval (spends money)")
    args = ap.parse_args()

    models, run = load_cfg()
    spent = ledger_total(run["cost"]["ledger_path"])
    if spent >= run["cost"]["ceiling_usd"]:
        print(f"COST CEILING hit (${spent:.2f} >= ${run['cost']['ceiling_usd']}). Refusing to launch.")
        sys.exit(2)
    if spent >= run["cost"]["warn_usd"]:
        print(f"WARNING: ${spent:.2f} spent, warn threshold ${run['cost']['warn_usd']}.")

    cmd = build_cmd(args.seeds, models, run, Path(args.log_dir))
    print("command:\n  " + " ".join(cmd))
    if not args.go:
        print("\n[dry run] pass --go to launch. (Requires: pip install inspect-ai inspect-petri, "
              "API keys for auditor/judge, and the vLLM target endpoint up.)")
        return
    Path(args.log_dir).mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)   # STUB boundary: real Petri run


if __name__ == "__main__":
    main()
