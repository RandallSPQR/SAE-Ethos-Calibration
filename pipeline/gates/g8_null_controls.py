"""G8 null_controls: validate the ANALYSIS MACHINERY, not just a shuffled vector.

Three checks, all exercising the real path in analyze/effects.py:
 (a) sparse-zero inclusion — a continuation where the feature never fires MUST still enter the analysis
     with mean 0 (regression test for the E[A|A>0] bug). If zero-activation uids vanish, fail.
 (b) end-to-end permutation null — permute labels at the INPUT and rerun the SAME aggregate→select→
     effect path; the null distribution of Cohen's d must center near 0 and a planted real effect must
     stand out against it (perm p small). A biased upstream aggregation shows up here.
 (c) token-level prefix balance — destructive vs benign prefixes matched on DECISION_TOKEN_POSITION
     (tokens), not message count. Positional/context effects are token phenomena.

Real run uses the actual feature store + transcripts. Fixture builds a tiny real store in a temp dir and
runs the real functions over it (no GPU, no heavy deps)."""
import json
import statistics as st
import tempfile
from pathlib import Path

from ._common import GateResult, load_run_cfg
from analyze import effects

NAME = "G8_null_controls"
NEEDS_GPU = False


def _prefix_balance_tokens(transcripts_dir, replayed_dir):
    """Token-level decision position per label group, joining replayed metadata. Returns (gap, n_missing).
    Missing token positions are COUNTED — absence is not acceptable evidence."""
    from ._common import iter_merged
    d0, d1, missing = [], [], 0
    for r in iter_merged(transcripts_dir, replayed_dir):
        if not r.get("judge"):
            continue
        tok = (r.get("tokens") or {}).get("decision_token_position")
        if tok is None:
            missing += 1
            continue
        (d1 if r["judge"]["taxonomy"] in effects.DESTRUCTIVE_TAX else d0).append(tok)
    gap = abs(st.mean(d0) - st.mean(d1)) if (d0 and d1) else None
    return gap, missing


def run(cfg, paths):
    g = load_run_cfg()
    ci = Path(paths["features"]) / "concept_index.json"
    if not ci.exists():
        return GateResult(NAME, False, {"error": "features/concept_index.json missing (run discovery)"})
    concepts = json.loads(ci.read_text())
    rep = paths.get("replayed")
    worst_null = 0.0
    try:
        for rec in concepts.values():
            null = effects.build_null(paths["features"], paths["transcripts"], rec["feature"],
                                      trials=200, replayed_dir=rep, strict=True)
            worst_null = max(worst_null, st.mean(abs(x) for x in null))
    except effects.CardinalityError as e:
        return GateResult(NAME, False, {"error": f"cardinality: {e}"})
    gap, missing = _prefix_balance_tokens(paths["transcripts"], rep)
    # missing token metadata FAILS the gate — unavailable evidence is not acceptable evidence
    if missing > 0 or gap is None:
        return GateResult(NAME, False, {"error": f"token-level positions unavailable (missing={missing}); "
                                                 "run replay so decision_token_position exists"})
    ok = worst_null <= g["g8_null_cohens_d_max"] and gap <= g.get("g8_prefix_token_gap_max", 40)
    return GateResult(NAME, ok, {"null_mean_abs_d": round(worst_null, 3),
                                 "max_allowed": g["g8_null_cohens_d_max"],
                                 "prefix_token_gap": round(gap, 1)})


def _write_synthetic(root):
    """A store where the feature fires ONLY in a few continuations, most are zero, labels are random.
    Correct (zero-inclusive) aggregation must dilute to ~null; a planted-label variant must be detectable."""
    tr = root / "transcripts" / "arm_a"; tr.mkdir(parents=True)
    fe = root / "features" / "s" / "full"; fe.mkdir(parents=True)
    feat_rows, tx_rows = [], []
    import random
    rng = random.Random(0)
    for i in range(400):
        uid = f"s/seed_{50 + (i % 50):03d}/full/c{i:02d}"        # test-split seeds
        lab = "deception_concealment" if i % 2 else "benign"
        tx_rows.append({"uid": uid, "scenario": "s", "seed": 50 + (i % 50), "variant": "full",
                        "judge": {"taxonomy": lab},
                        "tokens": {"assistant_token_count": 100, "decision_token_position": 800 + rng.randint(0, 5)}})
        # feature fires (nonzero rows) for only ~15 uids, at a few positions; the rest have NO rows (zero)
        if i < 15:
            for pos in range(3):
                feat_rows.append({"uid": uid, "position": pos, "in_assistant_span": True,
                                  "feature": 7, "activation": 5.0, "layer": 31})
    (tr / "s.jsonl").write_text("\n".join(json.dumps(r) for r in tx_rows))
    (fe / "s.jsonl").write_text("\n".join(json.dumps(r) for r in feat_rows))


def fixture():
    g = load_run_cfg()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); _write_synthetic(root)
        feats, trans = str(root / "features"), str(root / "transcripts")
        # (a) zero-inclusion: every labeled uid present, most with mean 0
        means, labels, _ = effects.mean_activation_per_uid(feats, trans, 7)
        zero_incl = len(means) == len(labels) and sum(1 for v in means.values() if v == 0) > 300
        # (b) permutation null centers near 0 through the REAL path
        null = effects.build_null(feats, trans, 7, trials=200)
        null_ok = st.mean(abs(x) for x in null) <= g["g8_null_cohens_d_max"] + 0.02
        # (c) token-level balance available (and missing-count is zero in the synthetic set)
        bal, missing = _prefix_balance_tokens(trans, None)
        ok = zero_incl and null_ok and (bal is not None) and missing == 0
        return GateResult(NAME + "[fixture]", ok,
                          {"zero_uids_included": zero_incl, "null_mean_abs_d": round(st.mean(abs(x) for x in null), 3),
                           "prefix_token_gap": None if bal is None else round(bal, 1), "missing": missing})
