"""Steering experiments (card Fig. 4.5.3.2.A and 4.5.3.3.A). Sweeps a vector across strengths and
measures the change in destructive-action rate (and, for G4, an easy calibration readout). The forward
with injection lives in replay/hooks.teacher_forced_forward(steer=...); this module orchestrates the
sweep, resampling continuations under each steering condition and re-judging. # STUB at the GPU
boundary; sweep bookkeeping and the dose-response assembly are real."""
import argparse
import json
from pathlib import Path
import yaml

CFG = Path(__file__).resolve().parent.parent / "config"


def sweep_conditions():
    run = yaml.safe_load((CFG / "run.yaml").read_text())
    s = run["steering"]
    return [(vec, strength) for vec in s["vectors"] for strength in s["sweep"]]


def assemble_curve(judged_by_condition):
    """judged_by_condition: {(vector,strength): [0/1 destructive labels]} -> per-vector dose-response."""
    curves = {}
    for (vec, strength), labels in judged_by_condition.items():
        rate = sum(labels) / len(labels) if labels else None
        curves.setdefault(vec, {})[strength] = rate
    return curves


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    args = ap.parse_args()
    conds = sweep_conditions()
    print(f"{len(conds)} steering conditions:",
          ", ".join(f"{v}@{s:+}" for v, s in conds[:6]), "...")
    if not args.go:
        print("[dry run] wiring OK. On a GPU box each condition resamples via replay/hooks with steer set.")
        return
    raise NotImplementedError("STUB: for each condition, resample continuations with steer=(vec,strength), "
                              "re-judge, then assemble_curve(); write features/steering_report.json.")


if __name__ == "__main__":
    main()
