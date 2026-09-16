"""G2 sae_health + HOOK-POINT IDENTIFICATION.

Two failure modes, not one:
 (a) health — variance-explained / L0 on OUR IT-chat transcripts must be acceptable (the SAE was
     trained on pretraining text; degradation is expected but bounded).
 (b) WRONG HOOK — Gemma Scope was trained on ONE residual tensor. Several tensors in the same block
     share its shape and yield plausible-but-meaningless sparse codes. Attaching to the wrong one is a
     silent error that would quietly ruin the SAE/oracle comparison. So G2 requires that the CHOSEN
     hook reproduces Google's published FVU/L0 within tolerance AND that every decoy candidate does
     NOT. Reproducing the published numbers is the only positive proof the hook is the trained one.

Real run reads features/sae_health.json, which replay.sae must write per candidate:
    {"chosen": {"hook": "...", "var_explained": .., "l0": ..},
     "candidates": [{"hook": "...", "var_explained": .., "l0": ..}, ...],
     "published": {"fvu": .., "l0": .., "tol": ..}}
fixture proves the identification logic on synthetic numbers."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg

NAME = "G2_sae_health"
NEEDS_GPU = True


def _matches_published(ve, l0, pub):
    """True if (fvu, l0) sit within relative tolerance of the published values. fvu = 1 - var_explained.
    If only one of the two numbers is published, match on that one (the report says which)."""
    if pub.get("fvu") is None and pub.get("l0") is None:
        return None                                  # can't verify without any numbers
    tol = pub.get("tol", 0.15)
    ok = True
    if pub.get("fvu") is not None:
        fvu = 1.0 - ve
        ok = ok and abs(fvu - pub["fvu"]) <= tol * max(pub["fvu"], 1e-6)
    if pub.get("l0") is not None:
        ok = ok and abs(l0 - pub["l0"]) <= tol * max(pub["l0"], 1e-6)
    return ok


def _evaluate(report, g):
    pub = report["published"]
    chosen = report["chosen"]
    health_ok = chosen["var_explained"] >= g["g2_var_explained_min"] and chosen["l0"] <= g["g2_l0_max"]
    chosen_match = _matches_published(chosen["var_explained"], chosen["l0"], pub)
    # every decoy must FAIL to match the published numbers; if any decoy matches, the hook is ambiguous
    decoys_reject = all(_matches_published(c["var_explained"], c["l0"], pub) is not True
                        for c in report.get("candidates", []))
    detail = {"chosen_hook": chosen["hook"], "var_explained": round(chosen["var_explained"], 3),
              "l0": round(chosen["l0"], 1), "matches_published": chosen_match,
              "decoys_rejected": decoys_reject, "health_ok": health_ok}
    if chosen_match is None:
        detail["warning"] = "published FVU/L0 not filled in models.yaml -> hook UNVERIFIED"
    elif pub.get("fvu") is None:
        detail["fvu_unverified"] = True              # matched on L0 only; no published FVU exists for this SAE
    elif pub.get("l0") is None:
        detail["l0_unverified"] = True
    ok = bool(health_ok and chosen_match and decoys_reject)
    return ok, detail


def run(cfg, paths):
    g = load_run_cfg()
    p = Path(paths["features"]) / "sae_health.json"
    if not p.exists():
        return GateResult(NAME, False, {"error": "features/sae_health.json missing (run replay --go)"})
    ok, detail = _evaluate(json.loads(p.read_text()), g)
    return GateResult(NAME, ok, detail)


def fixture():
    g = load_run_cfg()
    # published: fvu 0.12, l0 60. Correct hook reproduces them; decoys are off; health passes.
    report = {
        "published": {"fvu": 0.12, "l0": 60, "tol": 0.15},
        "chosen": {"hook": "layers.31.hook_resid_post", "var_explained": 0.88, "l0": 62},   # fvu 0.12
        "candidates": [
            {"hook": "layers.31.input_resid", "var_explained": 0.55, "l0": 140},        # off
            {"hook": "layers.31.mlp_output", "var_explained": 0.70, "l0": 95},             # off
        ],
    }
    ok, detail = _evaluate(report, g)
    # also prove it REJECTS a decoy that happens to match published (ambiguous hook)
    ambiguous = json.loads(json.dumps(report))
    ambiguous["candidates"][0] = {"hook": "decoy", "var_explained": 0.88, "l0": 61}
    ok2, _ = _evaluate(ambiguous, g)
    return GateResult(NAME + "[fixture]", ok and not ok2,
                      {"correct_hook_passes": ok, "ambiguous_hook_blocked": not ok2, **detail})
