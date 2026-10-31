#!/usr/bin/env python3
"""Run the instrument gates. Exit non-zero if any required gate fails — this is what blocks the
pipeline from spending money past a broken instrument.

  python gates/run_gates.py --fixture            # prove gate LOGIC on synthetic data (no deps, no GPU)
  python gates/run_gates.py --nogpu              # run G6/G7/G8 on real saved outputs
  python gates/run_gates.py                      # run all gates on real outputs (needs GPU for G1-G5)

Paths default to the standard tree; override with --transcripts/--features/--hand-labels.
"""
import argparse
import sys
from pathlib import Path

from . import (g0_model_checksum, g1_replay_fidelity, g2_sae_health, g3_feature_known_answer,
               g4_steering_known_answer, g5_oracle_calibration, g6_judge_agreement,
               g7_scenario_base_rates, g8_null_controls, g9_probe_external_fixture)

# G0-G5 are the T1 instrument-calibration ladder (run in order on the dev model, cheapest signal first);
# G6-G8 are the analysis gates that run on saved outputs without a GPU.
GATES = [g0_model_checksum, g1_replay_fidelity, g2_sae_health, g3_feature_known_answer,
         g4_steering_known_answer, g5_oracle_calibration, g6_judge_agreement,
         g7_scenario_base_rates, g8_null_controls, g9_probe_external_fixture]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--nogpu", action="store_true")
    ap.add_argument("--run-dir", default=None, help="runs/<run_id>/ — sets transcripts/replayed/features under it")
    ap.add_argument("--transcripts", default="transcripts")
    ap.add_argument("--replayed", default="replayed")
    ap.add_argument("--features", default="features")
    ap.add_argument("--hand-labels", default="eval/hand_labels.jsonl")
    ap.add_argument("--gates", default=None,
                    help="comma-separated subset by prefix, e.g. G0,G1,G2 (T1 ladder); default all")
    args = ap.parse_args()
    only = {g.strip().upper() for g in args.gates.split(",")} if args.gates else None
    if args.run_dir:
        args.transcripts = str(Path(args.run_dir) / "generation")
        args.replayed = str(Path(args.run_dir) / "replay")
        args.features = str(Path(args.run_dir) / "features")
    paths = {"transcripts": args.transcripts, "replayed": args.replayed,
             "features": args.features, "hand_labels": args.hand_labels,
             "probe": str(Path(args.run_dir) / "probe") if args.run_dir else None,
             "cardinality": str(Path(args.run_dir) / "cardinality.json") if args.run_dir else None}

    results = []
    for g in GATES:
        if args.nogpu and g.NEEDS_GPU:
            continue
        if only is not None and g.NAME.split("_")[0].upper() not in only:
            continue
        try:
            r = g.fixture() if args.fixture else g.run({}, paths)
        except Exception as e:                     # a gate that errors is a failed gate
            from ._common import GateResult
            r = GateResult(g.NAME, False, {"exception": type(e).__name__ + ": " + str(e)[:80]})
        results.append(r)
        print(r.line())

    failed = [r for r in results if r.status == "fail"]
    not_eval = [r for r in results if r.status == "not_evaluable"]
    mode = "fixture" if args.fixture else ("nogpu" if args.nogpu else "full")
    print(f"\n{mode}: {len(results) - len(failed) - len(not_eval)}/{len(results)} gates passed"
          + (f", {len(not_eval)} not evaluable (underpowered: neither pass nor fail)" if not_eval else "") + ".")
    if failed:
        print("BLOCKED by:", ", ".join(r.name for r in failed))
    if not_eval:
        print("NOT EVALUABLE (blocks spend until powered):", ", ".join(r.name for r in not_eval))
    if failed or not_eval:
        sys.exit(1)
    print("All run gates green.")


if __name__ == "__main__":
    main()
