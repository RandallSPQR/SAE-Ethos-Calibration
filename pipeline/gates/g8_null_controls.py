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
import math
import statistics as st
import tempfile
from pathlib import Path

from ._common import GateResult, load_run_cfg, GATE_RULES_VERSION, NOT_EVALUABLE
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


def null_bound(g, n1, n2):
    """Rules 2026-09-24.1: the permuted-label null mean |d| is bounded by max(floor, factor x E|d|), with
    E|d| = sqrt(2/pi) sqrt(1/n1 + 1/n2) the expectation of |d| for two null groups of those sizes. The old
    fixed 0.10 was below what a correct pipeline produces at 40 vs 530 (0.13)."""
    exp = math.sqrt(2 / math.pi) * math.sqrt(1 / max(1, n1) + 1 / max(1, n2))
    return max(g["g8_null_cohens_d_max"], g.get("g8_null_d_factor", 1.5) * exp), exp


def evaluate(g, features, transcripts, concepts, replayed=None, split="test", trials=200):
    """(status, detail). NOT_EVALUABLE when the reporting split has fewer than g8_min_group destructive or
    benign uids: an empty split makes every permuted d = 0.0, and a null that passes vacuously is the
    worst kind of green (rules 2026-09-24.1)."""
    min_group = int(g.get("g8_min_group", 20))
    worst_null, worst_bound, counts = 0.0, 0.0, {}
    try:
        for name, rec in concepts.items():
            eff = effects.run_effect(features, transcripts, rec["feature"], split=split, replayed_dir=replayed)
            n1, n2 = eff["n_destructive"], eff["n_benign"]
            counts[name] = {"n_destructive": n1, "n_benign": n2}
            if n1 < min_group or n2 < min_group:
                return NOT_EVALUABLE, {"reason": f"reporting split '{split}' has {n1} destructive / {n2} benign uids "
                                                 f"for {name}; need >= {min_group} each", "counts": counts,
                                       "rules": GATE_RULES_VERSION}
            null = effects.build_null(features, transcripts, rec["feature"], split=split, trials=trials,
                                      replayed_dir=replayed, strict=True)
            bound, exp = null_bound(g, n1, n2)
            worst_null = max(worst_null, st.mean(abs(x) for x in null)); worst_bound = max(worst_bound, bound)
    except effects.CardinalityError as e:
        return "fail", {"error": f"cardinality: {e}", "rules": GATE_RULES_VERSION}
    gap, missing = _prefix_balance_tokens(transcripts, replayed)
    # missing token metadata FAILS the gate: unavailable evidence is not acceptable evidence
    if missing > 0 or gap is None:
        return "fail", {"error": f"token-level positions unavailable (missing={missing}); run replay so "
                                 "decision_token_position exists", "rules": GATE_RULES_VERSION}
    ok = worst_null <= worst_bound and gap <= g.get("g8_prefix_token_gap_max", 40)
    return ("pass" if ok else "fail"), {"null_mean_abs_d": round(worst_null, 3), "null_bound": round(worst_bound, 3),
                                        "counts": counts, "split": split, "prefix_token_gap": round(gap, 1),
                                        "rules": GATE_RULES_VERSION}


def run(cfg, paths):
    g = load_run_cfg()
    ci = Path(paths["features"]) / "concept_index.json"
    if not ci.exists():
        return GateResult(NAME, False, {"error": "features/concept_index.json missing (run discovery)"})
    status, detail = evaluate(g, paths["features"], paths["transcripts"], json.loads(ci.read_text()),
                              replayed=paths.get("replayed"))
    return GateResult(NAME, status == "pass", detail, status=status)


def _write_synthetic(root, seeds=None, planted=False):
    """A store where the feature fires ONLY in a few continuations, most are zero, labels are random.
    Correct (zero-inclusive) aggregation must dilute to ~null; a planted-label variant must be detectable.
    seeds: the seed pool (odd = TEST under rules 2026-09-24.1); planted: the feature fires on destructive uids."""
    tr = root / "transcripts" / "arm_a"; tr.mkdir(parents=True)
    fe = root / "features" / "s" / "full"; fe.mkdir(parents=True)
    feat_rows, tx_rows = [], []
    import random
    rng = random.Random(0)
    seeds = seeds or [51 + 2 * k for k in range(25)]            # odd seeds: the reporting (test) split
    for i in range(400):
        seed = seeds[i % len(seeds)]
        uid = f"s/seed_{seed:03d}/full/c{i:02d}"
        lab = "deception_concealment" if i % 2 else "benign"
        tx_rows.append({"uid": uid, "scenario": "s", "seed": seed, "variant": "full",
                        "judge": {"taxonomy": lab},
                        "tokens": {"assistant_token_count": 100, "decision_token_position": 800 + rng.randint(0, 5)}})
        # feature fires (nonzero rows) for only ~15 uids, at a few positions; the rest have NO rows (zero);
        # planted: it fires on every destructive uid instead (a real effect the null must expose)
        if (planted and lab != "benign") or (not planted and i < 15):
            for pos in range(3):
                # spread within the group: identical values give a zero pooled sd and cohens_d returns 0.0
                feat_rows.append({"uid": uid, "position": pos, "in_assistant_span": True,
                                  "feature": 7, "activation": 4.0 + 2.0 * rng.random(), "layer": 31})
    (tr / "s.jsonl").write_text("\n".join(json.dumps(r) for r in tx_rows))
    (fe / "s.jsonl").write_text("\n".join(json.dumps(r) for r in feat_rows))


def fixture():
    g = load_run_cfg()
    concepts = {"c": {"feature": 7, "selection_seeds": [0, 2, 4]}}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); _write_synthetic(root)
        feats, trans = str(root / "features"), str(root / "transcripts")
        # (a) zero-inclusion: every labeled uid present, most with mean 0
        means, labels, _ = effects.mean_activation_per_uid(feats, trans, 7)
        zero_incl = len(means) == len(labels) and sum(1 for v in means.values() if v == 0) > 300
        # (b) the real path passes on a label-independent store, with the n-scaled bound
        status_ok, det_ok = evaluate(g, feats, trans, concepts, trials=100)
        # (c) a planted real effect stands out: perm p small through the same path
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); _write_synthetic(root, planted=True)
        feats, trans = str(root / "features"), str(root / "transcripts")
        eff = effects.run_effect(feats, trans, 7)
        null = effects.build_null(feats, trans, 7, trials=100)
        planted_p = (sum(1 for x in null if abs(x) >= abs(eff["cohens_d"])) + 1) / (len(null) + 1)
    with tempfile.TemporaryDirectory() as td:
        # (d) an EMPTY reporting split (all even = discover seeds) is NOT_EVALUABLE, never a pass
        root = Path(td); _write_synthetic(root, seeds=[0, 2, 4, 6])
        status_empty, det_empty = evaluate(g, str(root / "features"), str(root / "transcripts"), concepts, trials=10)
    bound, exp = null_bound(g, 200, 200)
    ok = (zero_incl and status_ok == "pass" and planted_p < 0.05 and status_empty == NOT_EVALUABLE
          and bound >= g["g8_null_cohens_d_max"])
    return GateResult(NAME + "[fixture]", ok,
                      {"zero_uids_included": zero_incl, "null_path": status_ok, "null_mean_abs_d": det_ok.get("null_mean_abs_d"),
                       "null_bound": det_ok.get("null_bound"), "planted_perm_p": round(planted_p, 3),
                       "empty_split_is_not_evaluable": status_empty == NOT_EVALUABLE,
                       "prefix_token_gap": det_ok.get("prefix_token_gap"), "rules": GATE_RULES_VERSION})
