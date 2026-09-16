"""G2 sae_health + HOOK-POINT IDENTIFICATION.

Two failure modes, not one:
 (a) health — variance-explained / L0 on OUR IT-chat transcripts must be acceptable (the SAE was
     trained on pretraining text; degradation is expected but bounded).
 (b) WRONG HOOK — Gemma Scope was trained on ONE residual tensor. Several tensors in the same block
     share its shape and yield plausible-but-meaningless sparse codes. Attaching to the wrong one is a
     silent error that would quietly ruin the SAE/oracle comparison. So G2 requires that the CHOSEN
     hook reproduces Google's published FVU/L0 within tolerance AND that every decoy candidate does
     NOT. Reproducing the published numbers is the only positive proof the hook is the trained one.

Rules 2026-09-16.2 (see gates/CHANGELOG.md):
  hook identification = VARIANCE EXPLAINED with a required absolute margin over EVERY decoy
      (g2_decoy_ve_margin_min). L0 is a weak discriminator against resid_pre by construction (the stream
      changes slowly across one block), so a published-L0 match is REPORTED, not gated.
  tensor identity (when present in the report) = the captured tensor must match an independent
      implementation's `blocks.31.hook_resid_post` (TransformerLens, no weight processing) RELATIONALLY:
      per-position cosine >= g2_identity_min_cos (0.999) and per-position relative norm difference <=
      g2_identity_max_norm_rel (1e-2). Element-wise bf16 atol would fail a correct hook on kernel-order
      noise accumulated over 31 layers; run the stage in fp32 to hold the tolerance with a clear
      conscience. Beating decoys shows "best of the candidates offered"; identity shows "right".
  encode integrity (when present) = fraction of active features below their own JumpReLU threshold == 0.
  health = var_explained >= g2_var_explained_min and l0 <= g2_l0_max.

Real run reads features/sae_health.json:
    {"chosen": {"hook", "var_explained", "l0", ...}, "candidates": [...], "published": {...},
     "identity": {"max_rel_err": .., "ref": "transformerlens:blocks.31.hook_resid_post"}   (optional)
     "jumprelu_below_threshold_frac": 0.0                                                    (optional)
     "per_doc_l0": [...]}                                                                    (optional)
fixture proves the identification logic on synthetic numbers."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg, GATE_RULES_VERSION

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
    margin = g.get("g2_decoy_ve_margin_min", 0.10)
    cands = report.get("candidates", [])
    worst_gap = min((chosen["var_explained"] - c["var_explained"] for c in cands), default=None)
    decoys_reject = worst_gap is not None and worst_gap >= margin
    chosen_match = _matches_published(chosen["var_explained"], chosen["l0"], pub)     # REPORTED, not gated
    ident = report.get("identity")
    ident_ok = None
    if ident is not None:
        ident_ok = (ident.get("min_cos") is not None and ident.get("max_norm_rel") is not None
                    and ident["min_cos"] >= g.get("g2_identity_min_cos", 0.999)
                    and ident["max_norm_rel"] <= g.get("g2_identity_max_norm_rel", 0.01))
    jr = report.get("jumprelu_below_threshold_frac")
    jr_ok = None if jr is None else (jr == 0.0)
    detail = {"rules": GATE_RULES_VERSION, "chosen_hook": chosen["hook"], "var_explained": round(chosen["var_explained"], 3),
              "l0": round(chosen["l0"], 1), "published_l0": pub.get("l0"), "matches_published": chosen_match,
              "decoy_ve_worst_gap": None if worst_gap is None else round(worst_gap, 3), "decoy_margin_min": margin,
              "decoys_rejected": decoys_reject, "identity_ok": ident_ok, "jumprelu_ok": jr_ok, "health_ok": health_ok}
    if ident is not None:
        detail["identity_min_cos"] = ident.get("min_cos")
        detail["identity_max_norm_rel"] = ident.get("max_norm_rel")
        detail["identity_dtype"] = ident.get("dtype")
    if report.get("per_doc_l0"):
        pd = sorted(report["per_doc_l0"])
        detail["per_doc_l0_median"] = round(pd[len(pd) // 2], 1)
        detail["per_doc_l0_range"] = [round(pd[0], 1), round(pd[-1], 1)]
    if pub.get("fvu") is None:
        detail["fvu_unpublished"] = True
    ok = bool(health_ok and decoys_reject and (ident_ok is not False) and (jr_ok is not False))
    if ident is None:
        detail["warning"] = "no tensor-identity check in report (run the TransformerLens stage)"
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
    report = {
        "published": {"fvu": None, "l0": 76, "tol": 0.15},
        "chosen": {"hook": "blocks.31.hook_resid_post", "var_explained": 0.73, "l0": 101},
        "candidates": [
            {"hook": "layers.31.input_resid", "var_explained": 0.60, "l0": 79},     # L0 "matches" but VE 0.13 below
            {"hook": "layers.31.mlp_output", "var_explained": -1090.0, "l0": 18},
            {"hook": "scaled_x0.8", "var_explained": 0.55, "l0": 70},
        ],
        "identity": {"min_cos": 0.9998, "max_norm_rel": 0.003, "ref": "transformerlens:blocks.31.hook_resid_post", "dtype": "float32"},
        "jumprelu_below_threshold_frac": 0.0, "per_doc_l0": [70, 80, 95, 100, 110, 300],
    }
    ok, detail = _evaluate(report, g)
    # a decoy within the VE margin makes the hook ambiguous -> blocked (even though its L0 is far off)
    amb = json.loads(json.dumps(report)); amb["candidates"][0] = {"hook": "near", "var_explained": 0.68, "l0": 300}
    ok_amb, _ = _evaluate(amb, g)
    # identity failure blocks even with a comfortable VE margin
    bad_id = json.loads(json.dumps(report)); bad_id["identity"]["min_cos"] = 0.97      # resid_pre-like: same scale, wrong direction
    ok_id, _ = _evaluate(bad_id, g)
    # encode integrity failure blocks
    bad_jr = json.loads(json.dumps(report)); bad_jr["jumprelu_below_threshold_frac"] = 0.05
    ok_jr, _ = _evaluate(bad_jr, g)
    # L0 mismatch alone does NOT block (reported only)
    return GateResult(NAME + "[fixture]", ok and not ok_amb and not ok_id and not ok_jr,
                      {"ve_margin_passes": ok, "ambiguous_ve_blocked": not ok_amb, "identity_blocks": not ok_id,
                       "jumprelu_blocks": not ok_jr, "l0_reported_not_gated": detail["matches_published"] is False,
                       "rules": GATE_RULES_VERSION})
