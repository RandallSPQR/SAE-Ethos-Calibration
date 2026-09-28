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
    Missing token positions are COUNTED — absence is not acceptable evidence.
    Rules 2026-09-28.1: the gap is measured WITHIN (scenario, variant, seed) cells and averaged over cells that
    hold both groups (weighted by their size), matching the within-cell permutation null. Pooled across
    scenarios the gap is a composition artifact (destructive outcomes concentrate in the scenarios with the
    longest prompts: 100 tokens on the T3 mock), while inside a cell every continuation shares one prefix."""
    from ._common import iter_merged
    cells, missing = {}, 0
    for r in iter_merged(transcripts_dir, replayed_dir):
        if not r.get("judge"):
            continue
        tok = (r.get("tokens") or {}).get("decision_token_position")
        if tok is None:
            missing += 1
            continue
        c = cells.setdefault((r["scenario"], r["variant"], r["seed"]), ([], []))
        c[1 if r["judge"]["taxonomy"] in effects.DESTRUCTIVE_TAX else 0].append(tok)
    both = [(len(a) + len(b), abs(st.mean(a) - st.mean(b))) for a, b in cells.values() if a and b]
    gap = (sum(n * g for n, g in both) / sum(n for n, _ in both)) if both else None
    return gap, missing


def null_bound(g, n1, n2):
    """Rules 2026-09-24.1: the permuted-label null mean |d| is bounded by max(floor, factor x E|d|), with
    E|d| = sqrt(2/pi) sqrt(1/n1 + 1/n2) the expectation of |d| for two null groups of those sizes. The old
    fixed 0.10 was below what a correct pipeline produces at 40 vs 530 (0.13)."""
    exp = math.sqrt(2 / math.pi) * math.sqrt(1 / max(1, n1) + 1 / max(1, n2))
    return max(g["g8_null_cohens_d_max"], g.get("g8_null_d_factor", 1.5) * exp), exp


def evaluate(g, features, transcripts, concepts, replayed=None, split="test", trials=200, seeds=(0, 1, 2, 3, 4, 5, 6, 7),
             perm_fn=None, stat_fn=None):
    """(status, detail). NOT_EVALUABLE when the reporting split has fewer than g8_min_group destructive or
    benign uids IN MIXED CELLS: an empty split makes every permuted d = 0.0, and a null that passes vacuously is the
    worst kind of green (rules 2026-09-24.1).

    Rules 2026-09-28.4 (successor to 2026-09-28.2/.3, whose per-concept per-seed z on d, taken worst-of-480 against a
    single-test line, failed a correct pipeline three times in four: on a feature that fires in one continuation the
    null takes two values and "SE" means nothing). Three lines, all committed before any result they grade:
      (1) SUPPORT FLOOR: a concept firing in fewer than g8_support_min continuations across mixed cells on the split
          is reported UNTESTABLE (neither pass nor fail) — the same floor discovery applies at selection.
      (2) BIAS on the NUMERATOR (the weighted within-cell mean difference, mean-zero under permutation by symmetry):
          z = |mean|/SE over `trials` permutations, per testable concept and seed; the line is FAMILY-WISE over
          concepts x seeds (Bonferroni at g8_family_alpha: the two-sided normal quantile at alpha / N tests).
      (3) SCALE: null mean |d| over sqrt(2/pi)/sqrt(sum w_c) <= g8_null_d_factor (1.5, fixed 2026-09-24.1) for every
          testable concept and seed.
    Plus the within-cell prefix token gap. perm_fn/stat_fn are fixture-only hooks that plant a defective null path."""
    from statistics import NormalDist
    min_group = int(g.get("g8_min_group", 20))
    support_min = int(g.get("g8_support_min", 5))
    alpha = float(g.get("g8_family_alpha", 0.05))
    scale_max = float(g.get("g8_null_d_factor", 1.5))
    counts, per_seed, untestable = {}, {}, []
    worst_bias_z, worst_bias_at, worst_scale_ratio, worst_scale_at = 0.0, None, 0.0, None
    n_tests = 0
    try:
        for name, rec in concepts.items():
            eff = effects.run_effect(features, transcripts, rec["feature"], split=split, replayed_dir=replayed)
            n1, n2 = eff["n_destructive"], eff["n_benign"]
            counts[name] = {"n_destructive": n1, "n_benign": n2, "mixed_cells": eff.get("mixed_cells"),
                            "support_uids": eff.get("support_uids"), "support_cells": eff.get("support_cells")}
            if n1 < min_group or n2 < min_group:
                return NOT_EVALUABLE, {"reason": f"reporting split '{split}' has {n1} destructive / {n2} benign uids in mixed "
                                                 f"cells for {name}; need >= {min_group} each", "counts": counts,
                                       "rules": GATE_RULES_VERSION}
            if (eff.get("support_uids") or 0) < support_min:
                counts[name]["untestable"] = True; untestable.append(name)
                continue
            exp_abs = math.sqrt(2 / math.pi) / math.sqrt(max(eff.get("w_sum") or 1e-9, 1e-9))
            for sd in seeds:
                null = effects.build_null(features, transcripts, rec["feature"], split=split, trials=trials,
                                          replayed_dir=replayed, strict=True, seed=sd, perm_fn=perm_fn, stat_fn=stat_fn,
                                          return_parts=True)
                num, dd = null["num"], null["d"]
                se = st.pstdev(num) / math.sqrt(len(num))
                bias_z = (abs(st.mean(num)) / se) if se > 0 else (0.0 if abs(st.mean(num)) == 0 else float("inf"))
                scale_ratio = st.mean(abs(x) for x in dd) / exp_abs
                n_tests += 1
                ps = per_seed.setdefault(str(sd), {"worst_bias_z": 0.0, "worst_scale_ratio": 0.0})
                ps["worst_bias_z"] = max(ps["worst_bias_z"], round(bias_z, 2)); ps["worst_scale_ratio"] = max(ps["worst_scale_ratio"], round(scale_ratio, 2))
                if bias_z > worst_bias_z:
                    worst_bias_z, worst_bias_at = bias_z, f"{name}@seed{sd}"
                if scale_ratio > worst_scale_ratio:
                    worst_scale_ratio, worst_scale_at = scale_ratio, f"{name}@seed{sd}"
    except effects.CardinalityError as e:
        return "fail", {"error": f"cardinality: {e}", "rules": GATE_RULES_VERSION}
    gap, missing = _prefix_balance_tokens(transcripts, replayed)
    # missing token metadata FAILS the gate: unavailable evidence is not acceptable evidence
    if missing > 0 or gap is None:
        return "fail", {"error": f"token-level positions unavailable (missing={missing}); run replay so "
                                 "decision_token_position exists", "rules": GATE_RULES_VERSION}
    if n_tests == 0:
        return NOT_EVALUABLE, {"reason": f"no concept reaches the support floor ({support_min} firing continuations in mixed cells)",
                               "untestable": untestable, "counts": counts, "rules": GATE_RULES_VERSION}
    z_line = NormalDist().inv_cdf(1 - alpha / (2 * n_tests))
    bias_pass = worst_bias_z <= z_line
    scale_pass = worst_scale_ratio <= scale_max
    gap_pass = gap <= g.get("g8_prefix_token_gap_max", 40)
    ok = bias_pass and scale_pass and gap_pass
    return ("pass" if ok else "fail"), {"null_bias_worst_z": round(worst_bias_z, 2), "null_bias_worst_at": worst_bias_at,
                                        "null_bias_z_line_family_wise": round(z_line, 3), "n_tests": n_tests, "family_alpha": alpha,
                                        "bias_pass": bias_pass,
                                        "null_abs_scale_worst_ratio": round(worst_scale_ratio, 2), "null_abs_scale_worst_at": worst_scale_at,
                                        "null_abs_scale_max_ratio": scale_max, "scale_pass": scale_pass,
                                        "support_min": support_min, "untestable": untestable, "n_testable": len(concepts) - len(untestable),
                                        "seeds": list(seeds), "trials": trials, "per_seed": per_seed,
                                        "counts": counts, "split": split, "prefix_token_gap": round(gap, 1), "gap_pass": gap_pass,
                                        "rules": GATE_RULES_VERSION}


def run(cfg, paths):
    g = load_run_cfg()
    ci = Path(paths["features"]) / "concept_index.json"
    if not ci.exists():
        return GateResult(NAME, False, {"error": "features/concept_index.json missing (run discovery)"})
    status, detail = evaluate(g, paths["features"], paths["transcripts"], json.loads(ci.read_text()),
                              replayed=paths.get("replayed"))
    return GateResult(NAME, status == "pass", detail, status=status)


def _write_synthetic(root, seeds=None, planted=False, cell_offset=False, homogeneous_frac=0.0, one_shot=False):
    """A store where the feature fires ONLY in a few continuations, most are zero, labels are random.
    Correct (zero-inclusive) aggregation must dilute to ~null; a planted-label variant must be detectable.
    seeds: the seed pool (odd = TEST under rules 2026-09-24.1); planted: the feature fires on destructive uids."""
    tr = root / "transcripts" / "arm_a"; tr.mkdir(parents=True)
    fe = root / "features" / "s" / "full"; fe.mkdir(parents=True)
    feat_rows, tx_rows = [], []
    import random
    rng = random.Random(0)
    seeds = seeds or [51 + 2 * k for k in range(25)]            # odd seeds: the reporting (test) split
    homog = {sd for k, sd in enumerate(seeds) if k < int(round(homogeneous_frac * len(seeds)))}   # label-homogeneous cells
    for i in range(400):
        seed = seeds[i % len(seeds)]
        uid = f"s/seed_{seed:03d}/full/c{i:02d}"
        lab = "deception_concealment" if i % 2 else "benign"
        if seed in homog:                       # the whole cell carries one label (pre-4c pathology: 83 of 114 T3 cells)
            lab = "deception_concealment" if (seeds.index(seed) % 2) else "benign"
        tx_rows.append({"uid": uid, "scenario": "s", "seed": seed, "variant": "full",
                        "judge": {"taxonomy": lab},
                        "tokens": {"assistant_token_count": 100, "decision_token_position": 800 + rng.randint(0, 5)}})
        # feature fires (nonzero rows) for only ~15 uids, at a few positions; the rest have NO rows (zero);
        # planted: it fires on every destructive uid instead (a real effect the null must expose)
        if cell_offset:
            # a feature that reads the PREFIX (one value per cell = per seed, small noise within the cell)
            for pos in range(3):
                feat_rows.append({"uid": uid, "position": pos, "in_assistant_span": True,
                                  "feature": 7, "activation": 1.0 + 0.5 * seeds.index(seed) + 0.2 * rng.random(), "layer": 31})
        elif one_shot:
            if i == 0:                          # fires in exactly one continuation of one mixed cell
                for pos in range(3):
                    feat_rows.append({"uid": uid, "position": pos, "in_assistant_span": True,
                                      "feature": 7, "activation": 4.0 + 2.0 * rng.random(), "layer": 31})
        elif (planted and lab != "benign") or (not planted and i < 15):
            for pos in range(3):
                # spread within the group: identical values give a zero pooled sd and cohens_d returns 0.0
                feat_rows.append({"uid": uid, "position": pos, "in_assistant_span": True,
                                  "feature": 7, "activation": 4.0 + 2.0 * rng.random(), "layer": 31})
    (tr / "s.jsonl").write_text("\n".join(json.dumps(r) for r in tx_rows))
    (fe / "s.jsonl").write_text("\n".join(json.dumps(r) for r in feat_rows))


def fixture():
    """Rules 2026-09-28.4. A criterion that passes an unbiased pipeline AND fails a planted one is a gate; one loosened
    until the real store passes is not. (a) zero-inclusion; (b) an unbiased label-independent sparse store PASSES;
    (c) a planted real effect stands out (perm p small); (d) an all-discover store is NOT_EVALUABLE; (e) a one-shot
    feature is UNTESTABLE, neither graded nor passed; (f) PLANTED LEAK 1 — the null path leaves the TRUE labels in
    place in half the cells (a label leak into the null) with a label-planted feature: the numerator bias line must
    FAIL; (g) PLANTED LEAK 2 — a feature that reads prefix length (one value per cell) scored with the pre-4c POOLED
    statistic on a store where most cells are label-homogeneous: the bias line must FAIL (the pooled null is a
    constant nonzero); (h) PLANTED LEAK 3 — the 4c history: a label-planted feature, 80 % homogeneous cells, pooled
    statistic: the SCALE line must FAIL (the T3 mock's d ~ 1.3). The stratified path passes on both stores."""
    g = load_run_cfg()
    concepts = {"c": {"feature": 7, "selection_seeds": [0, 2, 4]}}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); _write_synthetic(root)
        feats, trans = str(root / "features"), str(root / "transcripts")
        means, labels, _ = effects.mean_activation_per_uid(feats, trans, 7)
        zero_incl = len(means) == len(labels) and sum(1 for v in means.values() if v == 0) > 300
        status_ok, det_ok = evaluate(g, feats, trans, concepts, trials=100, seeds=(0, 1))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); _write_synthetic(root, planted=True)
        feats, trans = str(root / "features"), str(root / "transcripts")
        eff = effects.run_effect(feats, trans, 7)
        null = effects.build_null(feats, trans, 7, trials=100)
        planted_p = (sum(1 for x in null if abs(x) >= abs(eff["cohens_d"])) + 1) / (len(null) + 1)
        # (f) planted leak 1: sticky labels in half the cells, same store (feature fires on destructive uids)
        def sticky(labels_, cells_, rng):
            out = effects.stratified_permutation(labels_, cells_, rng)
            for u in labels_:
                if cells_[u][2] % 4 == 1:
                    out[u] = labels_[u]
            return out
        status_leak1, det_leak1 = evaluate(g, feats, trans, concepts, trials=100, seeds=(0, 1), perm_fn=sticky)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); _write_synthetic(root, seeds=[0, 2, 4, 6])
        status_empty, det_empty = evaluate(g, str(root / "features"), str(root / "transcripts"), concepts, trials=10, seeds=(0,))
    with tempfile.TemporaryDirectory() as td:
        # (e) one-shot feature: untestable, reported, never graded
        root = Path(td); _write_synthetic(root, one_shot=True)
        status_one, det_one = evaluate(g, str(root / "features"), str(root / "transcripts"), concepts, trials=20, seeds=(0,))
    with tempfile.TemporaryDirectory() as td:
        # (g) planted leak 2: prefix-length feature, 80 % homogeneous cells, POOLED statistic (pre-4c) vs stratified
        root = Path(td); _write_synthetic(root, cell_offset=True, homogeneous_frac=0.8)
        feats, trans = str(root / "features"), str(root / "transcripts")
        pooled = lambda X, y, cid: effects.cohens_d_arrays(X[y == 1], X[y == 0])      # the pre-4c pooled two-group d
        status_leak2, det_leak2 = evaluate(g, feats, trans, concepts, trials=100, seeds=(0, 1), stat_fn=pooled)
        status_strat, det_strat = evaluate(g, feats, trans, concepts, trials=100, seeds=(0, 1))
    with tempfile.TemporaryDirectory() as td:
        # (h) planted leak 3, the 4c history itself: a LABEL-planted feature on a store where 80 % of cells are
        # label-homogeneous, scored with the pooled statistic under within-cell permutation (the T3 mock's d ~ 1.3
        # "null"): the SCALE line must fail; the stratified path on the same store passes
        root = Path(td); _write_synthetic(root, planted=True, homogeneous_frac=0.8)
        feats, trans = str(root / "features"), str(root / "transcripts")
        status_leak3, det_leak3 = evaluate(g, feats, trans, concepts, trials=100, seeds=(0, 1), stat_fn=pooled)
        status_strat3, det_strat3 = evaluate(g, feats, trans, concepts, trials=100, seeds=(0, 1))
    ok = (zero_incl and status_ok == "pass" and planted_p < 0.05 and status_empty == NOT_EVALUABLE
          and status_one == NOT_EVALUABLE and det_one.get("untestable") == ["c"]
          and status_leak1 == "fail" and det_leak1.get("bias_pass") is False
          and status_leak2 == "fail" and det_leak2.get("bias_pass") is False
          and status_leak3 == "fail" and det_leak3.get("scale_pass") is False
          and status_strat == "pass" and status_strat3 == "pass")
    return GateResult(NAME + "[fixture]", ok,
                      {"zero_uids_included": zero_incl, "unbiased_store": status_ok,
                       "unbiased_bias_z": det_ok.get("null_bias_worst_z"), "z_line": det_ok.get("null_bias_z_line_family_wise"),
                       "unbiased_scale_ratio": det_ok.get("null_abs_scale_worst_ratio"), "planted_perm_p": round(planted_p, 3),
                       "empty_split_is_not_evaluable": status_empty == NOT_EVALUABLE,
                       "one_shot_untestable": det_one.get("untestable"),
                       "leak1_sticky_labels": f"{status_leak1} (bias z {det_leak1.get('null_bias_worst_z')})",
                       "leak2_prefix_pooled": f"{status_leak2} (bias z {det_leak2.get('null_bias_worst_z')}, scale {det_leak2.get('null_abs_scale_worst_ratio')})",
                       "leak2_store_stratified": f"{status_strat} (bias z {det_strat.get('null_bias_worst_z')}, scale {det_strat.get('null_abs_scale_worst_ratio')})",
                       "leak3_label_planted_pooled_4c": f"{status_leak3} (bias z {det_leak3.get('null_bias_worst_z')}, scale {det_leak3.get('null_abs_scale_worst_ratio')})",
                       "leak3_store_stratified": f"{status_strat3} (bias z {det_strat3.get('null_bias_worst_z')}, scale {det_strat3.get('null_abs_scale_worst_ratio')})",
                       "prefix_token_gap": det_ok.get("prefix_token_gap"), "rules": GATE_RULES_VERSION})
