"""Manifest for the benign-action FPR set (2026-10-02, Randall): every source uid, and its overlap with the span study's
control counterparts, so no row is used as both calibration and evaluation in one analysis. `check_disjoint` is the guard
an analysis calls with the uids it evaluates on.

  python results/fpr_calibration/make_manifest.py
"""
import collections, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPAN_CONTROL = HERE.parent / "t4_27b_2026-09-30_t3/transgression_spans/control_spans.jsonl"


def check_disjoint(eval_uids, model="gemma-3-27b-it", manifest=HERE / "benign_control_actions_manifest.json"):
    """Raise if any uid an analysis EVALUATES on (for `model`) is in the FPR calibration set. uids repeat across models
    (the 9B and 27B runs share the scenario/seed/variant/cNN form), so the check is per model."""
    cal = set(json.loads(Path(manifest).read_text())["by_model"].get(model, []))
    both = sorted(set(eval_uids) & cal)
    if both:
        raise SystemExit(f"{len(both)} uids are in the FPR calibration set and in this analysis's evaluation set, e.g. {both[:3]}")


def main():
    acts = [json.loads(l) for l in open(HERE / "benign_control_actions.jsonl")]
    uids = sorted({f'{a["model"]}:{a["run_id"]}:{a["source_uid"]}' for a in acts})   # uids repeat across models
    by_model = collections.defaultdict(set)
    for a in acts:
        by_model[a["model"]].add(a["source_uid"])
    span_ctl = {json.loads(l)["uid"] for l in open(SPAN_CONTROL) if l.strip()}
    roles_used = {json.loads(l)["uid"] for l in open(SPAN_CONTROL) if l.strip()
                  if any(json.loads(l)["roles"].values())}
    m = {"version": "2026-10-02", "file": "benign_control_actions.jsonl", "n_actions": len(acts), "source_uids": uids, "source_uid_key": "model:run_id:uid",
         "by_model": {k: sorted(v) for k, v in by_model.items()},
         "model_family": "Gemma only (gemma-3-27b-it, gemma-2-9b-it); the out-of-family set (SWE-agent) is pending with Codex",
         "overlap": {"span_study_control_counterparts": {
             "file": str(SPAN_CONTROL.relative_to(HERE.parent.parent)),
             "uids_in_both": len(by_model["gemma-3-27b-it"] & span_ctl),
             "uids_in_both_with_a_counterpart_span": len(by_model["gemma-3-27b-it"] & roles_used),
             "rule": "an analysis that evaluates on span-study control counterparts must not calibrate on these rows: "
                     "split the 27B rows (e.g. by seed parity) or calibrate on the 9B rows and the out-of-family set"}}}
    (HERE / "benign_control_actions_manifest.json").write_text(json.dumps(m, indent=1))
    print({k: (len(v) if isinstance(v, list) else v) for k, v in m.items() if k != "by_model"})
    print("by model:", {k: len(v) for k, v in by_model.items()}, "| overlap:", m["overlap"]["span_study_control_counterparts"]["uids_in_both"],
          "rows,", m["overlap"]["span_study_control_counterparts"]["uids_in_both_with_a_counterpart_span"], "with a counterpart span")


if __name__ == "__main__":
    main()
