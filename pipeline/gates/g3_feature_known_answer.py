"""G3 feature_known_answer: labeled features must fire where they should. Two checks:
 (a) discrimination — a known-labeled feature (e.g. 'git commands') separates positions that are about
     its concept from positions that aren't, AUROC >= threshold;
 (b) planted-concept — inserting a secrecy paragraph into a benign transcript raises the secrecy
     feature's activation, AND the 'mention' variant does NOT raise the behavior features (the §4.5.2
     topic-vs-behavior filter is real).
Rules 2026-09-16.2: an SAE feature is SPARSE (it fires on a few tokens of its concept and is legitimately
zero elsewhere), so discrimination is scored on WINDOW-MAX activations (report field `concept_positions`
holds per-window maxima; `*_raw` holds the per-position arrays for the record), together with
fraction-active. A per-position AUROC against zeros scored the first run at 0.578 for a feature with
perfect specificity; see gates/CHANGELOG.md.
Real run reads features/known_answer_report.json; fixture proves the AUROC + filter logic, including the
sparse case."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg, GATE_RULES_VERSION

NAME = "G3_feature_known_answer"
NEEDS_GPU = True


def auroc(pos, neg):
    if not pos or not neg:
        return 0.5
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


MIN_WINDOWS = 10   # rules 2026-09-30.3: an anchor judged on fewer concept or prose windows is NOT_EVALUABLE


def _two_anchor(rep, thr):
    """Rules 2026-09-30.3: every pre-registered anchor must separate its own concept positions from prose
    (window-max AUROC >= threshold AND mean concept activation above mean prose activation); an anchor with fewer than
    MIN_WINDOWS windows on either side cannot be judged, and the gate says so rather than pass or fail."""
    from ._common import NOT_EVALUABLE
    det, ok, short = {"rules": GATE_RULES_VERSION, "construction": "two_anchor", "threshold": thr}, True, []
    for name, a in rep["anchors"].items():
        if (a.get("n_concept_windows") or 0) < MIN_WINDOWS or (a.get("n_other_windows") or 0) < MIN_WINDOWS:
            short.append(name)
            det[name] = {"feature": a["feature"], "n_concept_windows": a.get("n_concept_windows"), "n_other_windows": a.get("n_other_windows")}
            continue
        a_ok = a["auroc"] is not None and a["auroc"] >= thr and (a.get("mean_concept") or 0) > (a.get("mean_other") or 0)
        ok = ok and a_ok
        det[name] = {"feature": a["feature"], "auroc": round(a["auroc"], 3) if a["auroc"] is not None else None, "ok": a_ok}
    if short:
        return GateResult(NAME, False, {**det, "not_evaluable": short}, status=NOT_EVALUABLE)
    return GateResult(NAME, ok, det)


def run(cfg, paths):
    thr = load_run_cfg()["g3_known_feature_auroc_min"]
    p = Path(paths["features"]) / "known_answer_report.json"
    if not p.exists():
        return GateResult(NAME, False, {"error": "features/known_answer_report.json missing"})
    rep = json.loads(p.read_text())
    if rep.get("construction") == "two_anchor":
        return _two_anchor(rep, thr)
    if rep.get("error"):
        # no anchor feature chosen for this SAE: G3 is missing, which blocks like a failure but is not read as one
        from ._common import NOT_EVALUABLE
        return GateResult(NAME, False, {"rules": GATE_RULES_VERSION, "missing": rep["error"]}, status=NOT_EVALUABLE)
    a = auroc(rep["concept_positions"], rep["other_positions"])
    planted_ok = rep["planted_secrecy_activation"] > rep["baseline_secrecy_activation"]
    filter_ok = rep["mention_behavior_activation"] <= 1.5 * rep["control_behavior_activation"]
    ok = a >= thr and planted_ok and filter_ok
    return GateResult(NAME, ok, {"rules": GATE_RULES_VERSION, "statistic": rep.get("statistic", "unspecified"),
                                 "auroc": round(a, 3), "threshold": thr, "planted_ok": planted_ok,
                                 "topic_filter_ok": filter_ok, "frac_active": rep.get("frac_active")})


def window_max(a, w=16):
    return [max(a[i:i + w]) for i in range(0, len(a), w) if len(a[i:i + w]) >= w // 2]


def _two_anchor_fixture(thr):
    good = {"construction": "two_anchor", "anchors": {
        "json_structure": {"feature": 1, "auroc": 0.93, "mean_concept": 2.0, "mean_other": 0.1, "n_concept_windows": 40, "n_other_windows": 60},
        "code": {"feature": 2, "auroc": 0.88, "mean_concept": 1.5, "mean_other": 0.2, "n_concept_windows": 30, "n_other_windows": 60}}}
    weak = {**good, "anchors": {**good["anchors"], "code": {**good["anchors"]["code"], "auroc": 0.55}}}
    inverted = {**good, "anchors": {**good["anchors"], "json_structure": {**good["anchors"]["json_structure"], "mean_concept": 0.05}}}
    thin = {**good, "anchors": {**good["anchors"], "code": {**good["anchors"]["code"], "n_concept_windows": 4}}}
    return (_two_anchor(good, thr).status == "pass" and _two_anchor(weak, thr).status == "fail"
            and _two_anchor(inverted, thr).status == "fail" and _two_anchor(thin, thr).status == "not_evaluable")


def fixture():
    thr = load_run_cfg()["g3_known_feature_auroc_min"]
    a = auroc([0.9, 0.8, 0.85, 0.7], [0.1, 0.2, 0.05, 0.15])   # clean separation ~1.0
    filter_ok = 0.12 <= 1.5 * 0.10
    # the sparse case: a feature active on 15% of concept tokens and never elsewhere. Per-position AUROC
    # sits near 0.57 (ties on zeros); window-max AUROC is 1.0. The rule must score the latter.
    code = ([0.0] * 5 + [40.0] + [0.0] * 10) * 8
    prose = [0.0] * 128
    a_pos, a_win = auroc(code, prose), auroc(window_max(code), window_max(prose))
    sparse_ok = a_pos < thr and a_win >= thr
    two_ok = _two_anchor_fixture(thr)
    ok = a >= thr and (0.8 > 0.1) and filter_ok and sparse_ok and two_ok
    return GateResult(NAME + "[fixture]", ok, {"auroc": round(a, 3), "threshold": thr, "filter_ok": filter_ok,
                                               "two_anchor_ok": two_ok,
                                               "sparse_position_auroc": round(a_pos, 3), "sparse_window_auroc": round(a_win, 3),
                                               "sparse_case_ok": sparse_ok, "rules": GATE_RULES_VERSION})
