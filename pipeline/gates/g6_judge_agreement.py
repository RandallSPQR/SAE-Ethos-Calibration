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
    hand = {json.loads(l)["uid"]: json.loads(l)["taxonomy"] for l in open(hp) if l.strip()}
    judge = {}
    for tf in Path(paths["transcripts"]).rglob("*.jsonl"):
        for line in open(tf):
            r = json.loads(line)
            if r.get("judge"):
                judge[r["uid"]] = r["judge"]["taxonomy"]
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
