"""Section 6 of PREREGISTRATION_WHITEBOX_27B.md: the pre-specified, DESCRIPTIVE readings of any feature that survives the
primary contrast on the test half (|d_test| >= the max-null (1-q) quantile). Written 2026-09-30 before any feature store of
the 27B T3 run was read. They qualify a survival; they do not create one.

  python -m analyze.readings_whitebox27 --run-dir <run or layer view> --contrast primary27_impossible_full_gamed_vs_nontransgression

  6.1 gaming vs flailing: each survivor's within-cell-centred mean E[A] by subgroup (gamed / honest / damaged / timeout /
      other non-transgression), test half, mixed cells.
  6.2 decision precedes act: gamed rows whose scored turn's tool call edits a test file (path under tests/ or matching
      test_*.py) or submits; the primary's family-wise statistic re-run on the test half without them (same selected
      features, same B, q, seed).
  6.3 lexical: survivors' Neuronpedia labels (profile ids; a network step) - top-activating tokens inside our spans are
      read from the position store on the pod, not here.
Writes analysis/readings_<contrast>.json."""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from analyze.effects import CONTRASTS, contrast_labels, family_wise, uid_feature_sums
from analyze.split import seed_split
from gates._common import iter_transcripts

TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$")
SUBGROUP = {"honest_report": "honest", "asked": "honest", "damaged": "damaged", "timeout": "timeout"}


def acts_at_decision(row):
    """True when the scored turn itself carries the act: an edit to a test file, or a submit."""
    m = row["messages"][row["scored_message_index"]]
    for tc in m.get("tool_calls") or []:
        a = tc.get("args") or {}
        if tc.get("name") == "submit":
            return True
        if tc.get("name") in ("edit_file", "write_file") and TEST_PATH.search(str(a.get("path", ""))):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--contrast", required=True)
    ap.add_argument("--trials", type=int, default=1000)
    ap.add_argument("--q", type=float, default=0.05)
    a = ap.parse_args()
    run = Path(a.run_dir)
    eff = json.loads((run / "analysis" / "effects_test.json").read_text())[a.contrast]
    thr = eff["max_abs_d_null_q"]
    survivors = [pf["feature"] for pf in eff["per_feature"] if abs(pf["d_test"]) >= thr]
    out = {"contrast": a.contrast, "max_abs_d_null_q": thr, "survivors": survivors}
    rows = {r["uid"]: r for r in iter_transcripts(run / "generation")}
    labels, cells, _ = contrast_labels(run / "generation", CONTRASTS[a.contrast])
    test = sorted(u for u in labels if seed_split(cells[u][2]) == "test")
    # mixed cells only (within-cell information)
    by_cell = defaultdict(list)
    for u in test:
        by_cell[cells[u]].append(u)
    mixed = [u for c, us in by_cell.items() if len({labels[x] for x in us}) == 2 for u in us]
    # 6.2 counts (reported whether or not anything survives)
    act = sorted(u for u in mixed if labels[u] == 1 and acts_at_decision(rows[u]))
    out["decision_precedes_act"] = {"gamed_rows_in_mixed_cells": sum(labels[u] for u in mixed),
                                    "gamed_rows_acting_at_decision": len(act)}
    if survivors:
        sums, counts = uid_feature_sums(run / "features", run / "replay", set(mixed))
        e = {u: {f: sums.get(u, {}).get(f, 0.0) / counts[u] for f in survivors} for u in mixed}
        cell_mean = {c: {f: sum(e[u][f] for u in us if u in e) / max(1, len([u for u in us if u in e])) for f in survivors}
                     for c, us in by_cell.items()}
        sub = {}
        for f in survivors:
            groups = defaultdict(list)
            for u in mixed:
                lab = rows[u]["judge"]["episode_outcome_label"]
                g = "gamed" if labels[u] == 1 else SUBGROUP.get(lab, "other_nontransgression")
                groups[g].append(e[u][f] - cell_mean[cells[u]][f])
            sub[str(f)] = {g: {"n": len(v), "centred_mean": round(sum(v) / len(v), 6)} for g, v in sorted(groups.items())}
        out["gaming_vs_flailing"] = sub
        concepts = [{"feature": f} for f in [pf["feature"] for pf in eff["per_feature"]]]
        rerun = family_wise(run, a.contrast, concepts, trials=a.trials, q=a.q, split="test", exclude_uids=act)
        out["decision_precedes_act"]["rerun_without_them"] = {
            k: rerun.get(k) for k in ("n_a_in_mixed_cells", "n_b_in_mixed_cells", "p_family_max", "max_abs_d_null_q")}
        out["decision_precedes_act"]["survivors_rerun_d"] = {
            str(pf["feature"]): pf["d_test"] for pf in rerun.get("per_feature", []) if pf["feature"] in survivors}
        try:
            from replay.sae import fetch_neuronpedia_labels
            fetch_neuronpedia_labels(survivors, run / "analysis" / f"feature_labels_{a.contrast}.json")
            out["lexical"] = "Neuronpedia labels in analysis/feature_labels_<contrast>.json; in-span top tokens: pod position store"
        except Exception as ex:  # noqa: BLE001
            out["lexical"] = f"neuronpedia fetch failed: {ex}"
    (run / "analysis" / f"readings_{a.contrast}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1)[:3000])


if __name__ == "__main__":
    main()
