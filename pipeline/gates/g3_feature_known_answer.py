"""G3 feature_known_answer: labeled features must fire where they should. Two checks:
 (a) discrimination — a known-labeled feature (e.g. 'git commands') separates positions that are about
     its concept from positions that aren't, AUROC >= threshold;
 (b) planted-concept — inserting a secrecy paragraph into a benign transcript raises the secrecy
     feature's activation, AND the 'mention' variant does NOT raise the behavior features (the §4.5.2
     topic-vs-behavior filter is real).
Real run reads features/known_answer_report.json; fixture proves the AUROC + filter logic."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg

NAME = "G3_feature_known_answer"
NEEDS_GPU = True


def auroc(pos, neg):
    if not pos or not neg:
        return 0.5
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def run(cfg, paths):
    thr = load_run_cfg()["g3_known_feature_auroc_min"]
    p = Path(paths["features"]) / "known_answer_report.json"
    if not p.exists():
        return GateResult(NAME, False, {"error": "features/known_answer_report.json missing"})
    rep = json.loads(p.read_text())
    a = auroc(rep["concept_positions"], rep["other_positions"])
    planted_ok = rep["planted_secrecy_activation"] > rep["baseline_secrecy_activation"]
    filter_ok = rep["mention_behavior_activation"] <= 1.5 * rep["control_behavior_activation"]
    ok = a >= thr and planted_ok and filter_ok
    return GateResult(NAME, ok, {"auroc": round(a, 3), "threshold": thr,
                                 "planted_ok": planted_ok, "topic_filter_ok": filter_ok})


def fixture():
    thr = load_run_cfg()["g3_known_feature_auroc_min"]
    a = auroc([0.9, 0.8, 0.85, 0.7], [0.1, 0.2, 0.05, 0.15])   # clean separation ~1.0
    filter_ok = 0.12 <= 1.5 * 0.10
    ok = a >= thr and (0.8 > 0.1) and filter_ok
    return GateResult(NAME + "[fixture]", ok, {"auroc": round(a, 3), "threshold": thr, "filter_ok": filter_ok})
