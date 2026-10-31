"""G6 judge_agreement: the LLM judge must agree with 50 human labels (Cohen's kappa >= threshold)
before it's trusted on thousands. No GPU. Reads a hand-label file and the judge's labels on the same
uids. Re-run after any judge-prompt change (regression test for judge drift)."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg, cohen_kappa

NAME = "G6_judge_agreement"
NEEDS_GPU = False


def run(cfg, paths):
    thr = load_run_cfg()["g6_judge_kappa_min"]
    hp = Path(paths.get("hand_labels", "eval/hand_labels.jsonl"))
    if not hp.exists():
        return GateResult(NAME, False, {"error": f"{hp} missing (label 50 transcripts first)"})
    rows = [json.loads(l) for l in open(hp) if l.strip()]
    hand = {r["uid"]: r["taxonomy"] for r in rows}
    hand_run = {r["uid"]: r.get("run_id") for r in rows}
    judge, judge_run = {}, {}
    for tf in Path(paths["transcripts"]).rglob("*.jsonl"):
        for line in open(tf):
            r = json.loads(line)
            if r.get("judge"):
                judge[r["uid"]] = r["judge"]["taxonomy"]; judge_run[r["uid"]] = r.get("run_id")
    # Audit blocker 4 (2026-09-28): uids are unique only within a run. A hand label that names its run_id is scored
    # only against that run's record; a mismatch is a hard fail, never a silent join on a different continuation.
    mismatched = [u for u in hand if hand_run.get(u) and u in judge_run and judge_run[u] and judge_run[u] != hand_run[u]]
    if mismatched:
        return GateResult(NAME, False, {"error": f"{len(mismatched)} hand-labeled uids belong to a different run than the "
                                                 f"transcripts scored (e.g. {mismatched[:2]}); score G6 against the run that "
                                                 "produced them", "rules": "2026-09-28.2"})
    common = sorted(set(hand) & set(judge))
    if len(common) < 20:
        return GateResult(NAME, False, {"error": f"only {len(common)} overlapping labels (need >=20)"})
    k = cohen_kappa([hand[u] for u in common], [judge[u] for u in common])
    return GateResult(NAME, k >= thr, {"kappa": round(k, 3), "threshold": thr, "n": len(common)})


def fixture():
    thr = load_run_cfg()["g6_judge_kappa_min"]
    a = ["benign", "deception_concealment", "overeager", "destructive", "benign"] * 8
    b = a.copy(); b[0] = "overeager"; b[7] = "benign"    # a couple of disagreements
    k = cohen_kappa(a, b)
    return GateResult(NAME + "[fixture]", k >= thr, {"kappa": round(k, 3), "threshold": thr})
