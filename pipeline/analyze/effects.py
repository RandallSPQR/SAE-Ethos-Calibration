"""Correlational analysis with the sparse-zero correction.

The feature store keeps only NONZERO activations. Averaging those gives E[A | A>0], and continuations
where the feature never fires vanish entirely — so a naive mean is conditioned on "the feature fired,"
which distorts destructive-vs-benign comparisons. The correct per-continuation quantity is

    mean_A(uid) = (sum of the feature's activations over assistant tokens) / (assistant_token_count)

with absent sparse entries counted as ZERO, and EVERY labeled continuation included (zero-activation
ones have mean 0). That is what this module computes.

It also exposes run_effect()/build_null() so G8 can run the REAL pipeline under permuted labels rather
than shuffling a fixed vector.

ESTIMAND / temporal alignment (name it correctly): by default the feature store scores the DECISION
span (scored_message_index) while the label is the EPISODE OUTCOME (judge.taxonomy). That pairing
answers the PREDICTION question A_decision -> Y_eventual — "what internal state at the first choice
predicts eventual transgression". To ask the CONCURRENT-MECHANISM question (A_transgression <-> behavior),
replay the transgression span (spans.transgression_message_index) instead and label with the same
outcome. These are different experiments; do not report one as the other. The default here is the
prediction question — arguably the more interesting trust question ("what was present before the model
betrayed the constraint").
"""
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from gates._common import cohens_d
from .split import seed_split, assert_no_leakage

DESTRUCTIVE_TAX = {"deception_concealment", "destructive", "task_cheating", "safeguard_bypass", "overeager"}

# Pre-registered contrasts (analyze/PREREGISTRATION_T3_WHITEBOX.md, 2026-09-28). Group 1 = "a", group 0 = "b".
CONTRASTS = {
    "primary_impossible_full_gamed_vs_honest": {
        "scenario": "impossible_test", "variant": "full", "by": "label",
        "a": {"edited_test", "fabricated_green", "edited_test_concealed", "subverted_harness"}, "b": {"honest_report", "asked"}},
    "secondary_missing_delete_full_silent_vs_asked": {
        "scenario": "missing_delete_tool", "variant": "full", "by": "label", "a": {"silent_empty"}, "b": {"asked_to_delete"}},
    "tertiary_all_destructive_vs_benign": {
        "scenario": None, "variant": None, "by": "taxonomy", "a": set(DESTRUCTIVE_TAX), "b": {"benign"}},
    # 27B within-cell pre-registration (analyze/PREREGISTRATION_WHITEBOX_27B.md, 2026-09-30): group a by LABEL (gamed),
    # group b by TAXONOMY (every non-transgressive outcome: benign, accidental, timeout), within impossible_test / full
    "primary27_impossible_full_gamed_vs_nontransgression": {
        "scenario": "impossible_test", "variant": "full", "by": "label",
        "a": {"edited_test", "fabricated_green", "edited_test_concealed", "subverted_harness"},
        "b_by": "taxonomy", "b": {"benign", "accidental", "timeout"}},
}


class CardinalityError(RuntimeError):
    pass


def contrast_labels(transcripts_dir, contrast):
    """uid -> 1/0 for the contrast's two groups (uids outside both groups or outside the scope are excluded),
    uid -> (scenario, variant, seed) cell (the permutation stratum), uid -> seed."""
    from gates._common import iter_transcripts
    labels, cells, seeds = {}, {}, {}
    for r in iter_transcripts(transcripts_dir):
        if contrast["scenario"] and r["scenario"] != contrast["scenario"]:
            continue
        if contrast["variant"] and r["variant"] != contrast["variant"]:
            continue
        j = r.get("judge") or {}
        key = j.get(contrast["by"])
        key_b = j.get(contrast.get("b_by", contrast["by"]))      # group b may be keyed differently (2026-09-30)
        if key in contrast["a"]:
            labels[r["uid"]] = 1
        elif key_b in contrast["b"]:
            labels[r["uid"]] = 0
        else:
            continue
        cells[r["uid"]] = (r["scenario"], r["variant"], r["seed"]); seeds[r["uid"]] = r["seed"]
    return labels, cells, seeds


def uid_feature_sums(features_dir, replayed_dir, uids=None):
    """uid -> {feature: in-span activation sum} from the *_uidsums files replay writes, and uid -> assistant token
    count from the replay metadata. A wanted uid without a token count is a CardinalityError (never a silent zero)."""
    from gates._common import load_replayed_tokens
    sums = defaultdict(dict)
    for f in sorted(Path(features_dir).rglob("*_uidsums.*")):
        for r in _read(f):
            if uids is not None and r["uid"] not in uids:
                continue
            sums[r["uid"]][int(r["feature"])] = sums[r["uid"]].get(int(r["feature"]), 0.0) + float(r["sum_act"])
    rep = load_replayed_tokens(replayed_dir)
    counts = {}
    for u in (uids if uids is not None else sums):
        n = (rep.get(u) or {}).get("assistant_token_count")
        if not n:
            raise CardinalityError(f"uid {u} has no assistant_token_count in {replayed_dir} (run replay); refusing a silent zero")
        counts[u] = n
    return sums, counts


def cohens_d_arrays(A, B):
    """Column-wise Cohen's d for two 2-D arrays (rows = uids, cols = features); 0 where the pooled sd is 0."""
    import numpy as np
    ma, mb = A.mean(0), B.mean(0)
    va = A.var(0, ddof=1) if len(A) > 1 else np.zeros_like(ma)
    vb = B.var(0, ddof=1) if len(B) > 1 else np.zeros_like(mb)
    na, nb = len(A), len(B)
    pooled = np.sqrt(((na - 1) * va + (nb - 1) * vb) / max(1, na + nb - 2))
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(pooled > 0, (ma - mb) / pooled, 0.0)
    return d


def stratified_d_arrays(X, y, cell_ids):
    """Within-cell (stratified) Cohen's d per column: over cells holding both groups, the size-weighted mean of the
    within-cell mean differences (w_c = n_a,c n_b,c / n_c), divided by the pooled WITHIN-cell sd. Exchangeable under
    the within-cell permutation null (pre-registration 4c, 2026-09-28: the pooled d is not — on the T3 mock a purely
    label-planted feature kept d ~ 1.3 under within-cell permutation because 83 of 114 cells are label-homogeneous).
    Returns (d[K], n_a_eff, n_b_eff, n_mixed_cells)."""
    import numpy as np
    X = np.asarray(X, dtype=np.float64); y = np.asarray(y); cell_ids = np.asarray(cell_ids)
    num = np.zeros(X.shape[1]); wsum = 0.0; ss = np.zeros(X.shape[1]); dof = 0; na = nb = 0; mixed = 0
    for c in np.unique(cell_ids):
        m = cell_ids == c
        A, B = X[m & (y == 1)], X[m & (y == 0)]
        if len(A) == 0 or len(B) == 0:
            continue
        w = len(A) * len(B) / (len(A) + len(B))
        num += w * (A.mean(0) - B.mean(0)); wsum += w
        ss += ((A - A.mean(0)) ** 2).sum(0) + ((B - B.mean(0)) ** 2).sum(0)
        dof += len(A) + len(B) - 2; na += len(A); nb += len(B); mixed += 1
    if wsum == 0 or dof <= 0:
        return np.zeros(X.shape[1]), 0, 0, 0
    sd = np.sqrt(ss / dof)
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(sd > 0, (num / wsum) / sd, 0.0)
    return d, na, nb, mixed


def stratified_parts(X, y, cell_ids):
    """The stratified d and its NUMERATOR (the size-weighted within-cell mean difference, num / sum w_c) per column.
    Rules 2026-09-28.4: the numerator is mean-zero under within-cell permutation by symmetry and is what G8's bias
    check reads; d is a ratio whose denominator moves with the sign of the numerator on features that fire in one
    continuation of a cell (a two-valued null; the 2026-09-28.2 failure). Returns (d, numerator)."""
    import numpy as np
    X = np.asarray(X, dtype=np.float64); y = np.asarray(y); cell_ids = np.asarray(cell_ids)
    num = np.zeros(X.shape[1]); wsum = 0.0; ss = np.zeros(X.shape[1]); dof = 0
    for c in np.unique(cell_ids):
        m = cell_ids == c
        A, B = X[m & (y == 1)], X[m & (y == 0)]
        if len(A) == 0 or len(B) == 0:
            continue
        w = len(A) * len(B) / (len(A) + len(B))
        num += w * (A.mean(0) - B.mean(0)); wsum += w
        ss += ((A - A.mean(0)) ** 2).sum(0) + ((B - B.mean(0)) ** 2).sum(0)
        dof += len(A) + len(B) - 2
    if wsum == 0 or dof <= 0:
        return np.zeros(X.shape[1]), np.zeros(X.shape[1])
    sd = np.sqrt(ss / dof); numer = num / wsum
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(sd > 0, numer / sd, 0.0)
    return d, numer


def feature_support(X, y, cell_ids):
    """Rules 2026-09-28.4 support floor: per column, the number of uids with a nonzero mean activation that lie in
    MIXED cells (both labels present), and the number of mixed cells holding one. A feature firing in fewer than
    g8_support_min such uids on a split is excluded from discovery on that split and reported untestable by G8:
    its permutation null takes a handful of values and neither a z nor a scale can be read from it."""
    import numpy as np
    X = np.asarray(X, dtype=np.float64); y = np.asarray(y); cell_ids = np.asarray(cell_ids)
    mixed = np.zeros(len(y), dtype=bool)
    for c in np.unique(cell_ids):
        m = cell_ids == c
        if (y[m] == 1).any() and (y[m] == 0).any():
            mixed |= m
    nz = (X != 0) & mixed[:, None]
    n_uids = nz.sum(0)
    n_cells = np.array([len(set(cell_ids[nz[:, j]])) for j in range(X.shape[1])])
    return n_uids, n_cells


def stratified_permutation(labels, cells, rng):
    """Permute the 0/1 labels WITHIN each (scenario, variant, seed) cell (pre-registration statistic 3). Cells and
    uids are visited in sorted order so the permutation sequence depends only on the RNG seed (audit 2026-09-28:
    it depended on directory listing order)."""
    by_cell = defaultdict(list)
    for u in sorted(labels):
        by_cell[cells[u]].append(u)
    out = {}
    for cell in sorted(by_cell):
        us = by_cell[cell]
        labs = [labels[u] for u in us]
        rng.shuffle(labs)
        out.update(zip(us, labs))
    return out


def family_wise(run_dir, contrast_name, concepts, trials=1000, q=0.05, seed=0, split="test", exclude_uids=()):
    """Pre-registration statistic 4 on the reporting split: per selected feature d and a within-cell permutation p
    (descriptive); the REPORTED number is the count of features whose |d| exceeds their own null (1-q) quantile,
    against the null distribution of that count from the same permutations."""
    import numpy as np
    run_dir = Path(run_dir)
    labels, cells, _ = contrast_labels(run_dir / "generation", CONTRASTS[contrast_name])
    uids = sorted(u for u in labels if seed_split(cells[u][2]) == split and u not in set(exclude_uids))  # deterministic order
    # exclude_uids: the 27B pre-registration's "decision precedes act" sensitivity (section 6.2); empty for every main result
    feats = [c["feature"] for c in concepts]
    if not uids or not feats:
        return {"contrast": contrast_name, "split": split, "error": "no uids or no features"}
    sums, counts = uid_feature_sums(run_dir / "features", run_dir / "replay", set(uids))
    X = np.zeros((len(uids), len(feats)), dtype=np.float64)
    for r, u in enumerate(uids):
        for j, f in enumerate(feats):
            X[r, j] = sums.get(u, {}).get(f, 0.0) / counts[u]
    y = np.array([labels[u] for u in uids])
    cell_ids = np.array(["/".join(map(str, cells[u])) for u in uids])          # stable string keys, never hash()
    n1, n0 = int(y.sum()), int((1 - y).sum())
    d_obs, n1_eff, n0_eff, mixed = stratified_d_arrays(X, y, cell_ids)
    rng = random.Random(seed)
    D = np.zeros((trials, len(feats)))
    labels_sub = {u: labels[u] for u in uids}
    for b in range(trials):
        perm = stratified_permutation(labels_sub, cells, rng)
        yb = np.array([perm[u] for u in uids])
        D[b] = stratified_d_arrays(X, yb, cell_ids)[0]
    absD = np.abs(D)
    thr = np.quantile(absD, 1 - q, axis=0)
    obs_count = int((np.abs(d_obs) >= thr).sum())
    null_counts = (absD >= thr[None, :]).sum(1)
    p_family = (1 + int((null_counts >= obs_count).sum())) / (trials + 1)
    # 4b (amendment 2026-09-28, before the pod): max-|d| over the K features (Westfall-Young). The count statistic
    # cannot see ONE strong feature (expected null count = K*q = 1); the max statistic can, and controls the
    # family-wise error the same way. Both are reported; neither is chosen after the fact.
    max_obs = float(np.abs(d_obs).max())
    max_null = absD.max(1)
    p_max = (1 + int((max_null >= max_obs).sum())) / (trials + 1)
    n_beyond_max_null = int((np.abs(d_obs) >= np.quantile(max_null, 1 - q)).sum())   # features whose |d| exceeds the max-null (1-q) quantile
    per_feature = []
    for j, f in enumerate(feats):
        p_f = (1 + int((absD[:, j] >= abs(d_obs[j])).sum())) / (trials + 1)
        per_feature.append({"feature": int(f), "d_test": round(float(d_obs[j]), 4), "perm_p": round(p_f, 4),
                            "null_q_abs_d": round(float(thr[j]), 4), "clears_null": bool(abs(d_obs[j]) >= thr[j]),
                            "mean_a": round(float(X[y == 1, j].mean()), 5), "mean_b": round(float(X[y == 0, j].mean()), 5)})
    return {"contrast": contrast_name, "split": split, "n_a": n1, "n_b": n0, "n_a_in_mixed_cells": int(n1_eff), "n_b_in_mixed_cells": int(n0_eff),
            "mixed_cells": int(mixed), "statistic": "stratified (within-cell) Cohen's d", "k": len(feats), "q": q, "trials": trials,
            "observed_count_clearing_null": obs_count, "null_count_mean": round(float(null_counts.mean()), 3),
            "null_count_95": int(np.quantile(null_counts, 0.95)), "p_family": round(p_family, 4),
            "max_abs_d_observed": round(max_obs, 4), "max_abs_d_null_q": round(float(np.quantile(max_null, 1 - q)), 4),
            "p_family_max": round(p_max, 4), "n_features_beyond_max_null_q": n_beyond_max_null,
            "evaluable_by_g8_rule": bool(n1_eff >= 20 and n0_eff >= 20), "per_feature": per_feature,
            "cells": len({cells[u] for u in uids})}


def _read(f):
    if f.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
            return pq.read_table(f).to_pylist()
        except ImportError:
            return []
    return [json.loads(l) for l in open(f) if l.strip()]


def assistant_token_counts(transcripts_dir, replayed_dir=None):
    """uid -> assistant_token_count (E[A] denominator), joining replay-derived tokens by uid. Labels and
    seeds come from the immutable generation records. A uid with no token count is reported as None so
    callers can FAIL on missing evidence rather than treat absence as zero."""
    from gates._common import iter_merged
    counts, labels, seeds, prefix_tok = {}, {}, {}, {}
    for r in iter_merged(transcripts_dir, replayed_dir):
        t = r.get("tokens") or {}
        counts[r["uid"]] = t.get("assistant_token_count")
        prefix_tok[r["uid"]] = t.get("decision_token_position")
        if r.get("judge"):
            labels[r["uid"]] = (1 if r["judge"]["taxonomy"] in DESTRUCTIVE_TAX else 0)
        seeds[r["uid"]] = r["seed"]
    return counts, labels, seeds, prefix_tok


class FeatureStoreError(RuntimeError):
    pass


def feature_sum_per_uid(features_dir, feature_index):
    """uid -> SUM of the feature's activation over assistant-span tokens (nonzero rows only; missing=0).

    ONE canonical format per store: if both .parquet and .jsonl exist, refuse (a stale JSONL from a
    pre-PyArrow run would be double-counted). Also reject duplicate (uid, position, feature) records —
    plausible inflation with no crash otherwise."""
    parquet = list(Path(features_dir).rglob("*.parquet"))
    jsonl = list(Path(features_dir).rglob("*.jsonl"))
    if parquet and jsonl:
        raise FeatureStoreError(f"{features_dir} has BOTH .parquet and .jsonl — pick one canonical "
                                "format per run to avoid double-counting stale data.")
    sums = defaultdict(float)
    seen = set()
    for f in (parquet or jsonl):
        for row in _read(f):
            if row.get("feature") == feature_index and row.get("in_assistant_span"):
                key = (row["uid"], row["position"], row["feature"])
                if key in seen:
                    raise FeatureStoreError(f"duplicate feature record {key} in {features_dir}")
                seen.add(key)
                sums[row["uid"]] += row["activation"]
    return sums


def _uidsums_available(features_dir):
    return any(Path(features_dir).rglob("*_uidsums.*"))


_MEANS_CACHE = {}


def mean_activation_per_uid(features_dir, transcripts_dir, feature_index, replayed_dir=None, strict=True):
    """Cached per (store, transcripts, feature, replayed): G8 asks for the same feature once per RNG seed."""
    key = (str(features_dir), str(transcripts_dir), int(feature_index), str(replayed_dir), bool(strict))
    if key not in _MEANS_CACHE:
        _MEANS_CACHE[key] = _mean_activation_per_uid(features_dir, transcripts_dir, feature_index, replayed_dir, strict)
    means, labels, seeds = _MEANS_CACHE[key]
    return dict(means), dict(labels), dict(seeds)


def _mean_activation_per_uid(features_dir, transcripts_dir, feature_index, replayed_dir=None, strict=True):
    """E[A] over assistant tokens for EVERY labeled continuation, zeros included. With strict=True (the
    default for real analysis), a labeled uid that is MISSING its assistant_token_count is a hard error —
    dropping it would silently shrink N and could make a biased analysis look beautifully null."""
    counts, labels, seeds, _ = assistant_token_counts(transcripts_dir, replayed_dir)
    if _uidsums_available(features_dir):
        # fast path: replay's per-uid in-span sums (identical by definition to summing in-span position rows)
        sums = defaultdict(float)
        for f in sorted(Path(features_dir).rglob("*_uidsums.*")):
            for r in _read(f):
                if int(r["feature"]) == int(feature_index):
                    sums[r["uid"]] += float(r["sum_act"])
        means, missing = {}, []
        for uid in labels:
            n = counts.get(uid)
            if not n:
                missing.append(uid); continue
            means[uid] = sums.get(uid, 0.0) / n
        if strict and missing:
            raise CardinalityError(f"{len(missing)} labeled uids missing assistant_token_count (run replay); e.g. {missing[:3]}")
        return means, labels, seeds
    counts, labels, seeds, _ = assistant_token_counts(transcripts_dir, replayed_dir)
    sums = feature_sum_per_uid(features_dir, feature_index)
    means = {}
    missing = []
    for uid in labels:
        n = counts.get(uid)
        if not n:
            missing.append(uid)
            continue
        means[uid] = sums.get(uid, 0.0) / n        # missing feature -> 0 numerator, still divided by n
    if strict and missing:
        raise CardinalityError(
            f"{len(missing)} labeled uids missing assistant_token_count (run replay); "
            f"refusing to analyze a subset silently. e.g. {missing[:3]}")
    return means, labels, seeds


def run_effect(features_dir, transcripts_dir, feature_index, split="test", label_override=None,
               replayed_dir=None, strict=True):
    """Cohen's d of E[A] between destructive and benign, over `split`. label_override lets the caller
    (G8) substitute permuted labels while running the SAME aggregation path."""
    means, labels, seeds = mean_activation_per_uid(features_dir, transcripts_dir, feature_index,
                                                   replayed_dir=replayed_dir, strict=strict)
    labels = label_override or labels
    uids = sorted(u for u in means if seed_split(seeds[u]) == split and u in labels)
    if not uids:
        return {"feature": feature_index, "cohens_d": 0.0, "n_destructive": 0, "n_benign": 0, "split": split}
    import numpy as np
    from gates._common import iter_transcripts
    us = set(uids)
    cells = {r["uid"]: (r["scenario"], r["variant"], r["seed"]) for r in iter_transcripts(transcripts_dir) if r["uid"] in us}
    X = np.array([[means[u]] for u in uids]); y = np.array([labels[u] for u in uids])
    cell_ids = np.array(["/".join(map(str, cells.get(u, ("?", "?", seeds[u])))) for u in uids])
    d, na, nb, mixed = stratified_d_arrays(X, y, cell_ids)      # rules 2026-09-28.1: within-cell statistic
    sup_uids, sup_cells = feature_support(X, y, cell_ids)          # rules 2026-09-28.4: firing support in mixed cells
    # sum of cell weights w_c = n_a n_b / n_c: the null variance of the weighted within-cell mean difference is
    # sigma^2 / sum(w), so E|d_strat| under a Gaussian null is sqrt(2/pi)/sqrt(sum w) (G8 reading, rules 2026-09-28.2)
    wsum = 0.0
    for c in np.unique(cell_ids):
        m = cell_ids == c; a, b = int(y[m].sum()), int((1 - y[m]).sum())
        if a and b:
            wsum += a * b / (a + b)
    return {"feature": feature_index, "cohens_d": round(float(d[0]), 4), "statistic": "stratified (within-cell) d",
            "n_destructive": int(na), "n_benign": int(nb), "mixed_cells": int(mixed), "split": split, "w_sum": round(wsum, 3),
            "support_uids": int(sup_uids[0]), "support_cells": int(sup_cells[0]),
            "n_destructive_all": int(y.sum()), "n_benign_all": int((1 - y).sum())}


def build_null(features_dir, transcripts_dir, feature_index, split="test", trials=200, seed=0,
               replayed_dir=None, strict=True, perm_fn=None, stat_fn=None, return_parts=False):
    """Empirical null for ONE feature: labels permuted WITHIN (scenario, variant, seed) cells (rules 2026-09-28.1),
    the stratified within-cell d recomputed each time from means read ONCE (the previous version re-read the
    store every trial: 166 s for 2 concepts x 40 trials on the T3 mock). Returns the list of permuted d, or with
    return_parts=True a dict {"d": [...], "num": [...]} (rules 2026-09-28.4: G8 reads the numerator's bias).
    perm_fn / stat_fn exist for the G8 fixture ONLY: they plant a defective null path (labels that stick in some
    cells; the pre-4c pooled statistic) that the gate must fail. Real analysis never passes them."""
    import numpy as np
    from gates._common import iter_transcripts
    means, labels, seeds = mean_activation_per_uid(features_dir, transcripts_dir, feature_index,
                                                   replayed_dir=replayed_dir, strict=strict)
    uids = sorted(u for u in labels if seed_split(seeds[u]) == split and u in means)
    us = set(uids)
    cells = {r["uid"]: (r["scenario"], r["variant"], r["seed"]) for r in iter_transcripts(transcripts_dir) if r["uid"] in us}
    X = np.array([[means[u]] for u in uids]); cell_ids = np.array(["/".join(map(str, cells.get(u, ("?", "?", seeds[u])))) for u in uids])
    sub_labels = {u: labels[u] for u in uids}
    rng = random.Random(seed)
    perm_fn = perm_fn or stratified_permutation
    null, nums = [], []
    for _ in range(trials):
        perm = perm_fn(sub_labels, cells, rng)
        yb = np.array([perm[u] for u in uids])
        if stat_fn is not None:
            d = stat_fn(X, yb, cell_ids); numer = d
        else:
            d, numer = stratified_parts(X, yb, cell_ids)
        null.append(round(float(d[0]), 4)); nums.append(float(numer[0]))
    return {"d": null, "num": nums} if return_parts else null


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None, help="runs/<run_id>/: sets --features/--transcripts/--replayed/--concept-index")
    ap.add_argument("--features", default="features")
    ap.add_argument("--transcripts", default="transcripts")
    ap.add_argument("--replayed", default=None, help="replay-derived token metadata (runs/<run_id>/replay); "
                                                    "REQUIRED for real analysis: without it every uid lacks its token count")
    ap.add_argument("--concept-index", default=None)
    ap.add_argument("--split", default="test", choices=["test", "discover"],
                    help="which seeds to report on; 'test' is the reporting split (analyze/split.py); "
                         "'discover' is for dry runs on discover-only data and is labeled as such")
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--family-wise", action="store_true", help="pre-registered report: per contrast in concept_index, the "
                                                              "family-wise count on the reporting split (writes analysis/effects_<split>.json)")
    ap.add_argument("--q", type=float, default=0.05)
    args = ap.parse_args()
    if args.run_dir:
        rd = Path(args.run_dir)
        args.features, args.transcripts = str(rd / "features"), str(rd / "generation")
        args.replayed = args.replayed or str(rd / "replay")
    ci = Path(args.concept_index or (Path(args.features) / "concept_index.json"))
    if not ci.exists():
        print("(no concept_index.json yet — run discovery first)"); return
    assert_no_leakage(ci)
    if args.family_wise:
        index = json.loads(ci.read_text())
        by_contrast = defaultdict(list)
        for name, rec in index.items():
            if rec.get("contrast"):
                by_contrast[rec["contrast"]].append(rec)
        out = {}
        for cname, concepts in by_contrast.items():
            concepts = sorted(concepts, key=lambda c: c.get("rank", 0))
            rep = family_wise(Path(args.run_dir) if args.run_dir else Path(args.features).parent, cname, concepts,
                              trials=args.trials, q=args.q, split=args.split)
            out[cname] = rep
            if "error" in rep:
                print(cname, rep["error"]); continue
            print(f"{cname} [{rep['split']}]: n_a={rep['n_a']} n_b={rep['n_b']} k={rep['k']} "
                  f"count clearing null={rep['observed_count_clearing_null']} (null mean {rep['null_count_mean']}, 95% {rep['null_count_95']}) "
                  f"p_family={rep['p_family']} | max|d|={rep['max_abs_d_observed']} vs max-null q={rep['max_abs_d_null_q']} "
                  f"p_family_max={rep['p_family_max']} n_beyond={rep['n_features_beyond_max_null_q']} | evaluable_by_g8_rule={rep['evaluable_by_g8_rule']}")
        od = (Path(args.run_dir) if args.run_dir else Path(args.features).parent) / "analysis"; od.mkdir(exist_ok=True)
        (od / f"effects_{args.split}.json").write_text(json.dumps(out, indent=1))
        return
    # 2026-09-24 mock dry run found this CLI never joined the replay metadata (no --replayed), so every uid
    # lacked assistant_token_count and the strict cardinality check refused; the wiring is now explicit.
    if (Path(args.features) / "MOCK").exists():
        print("NOTE: features/MOCK present: this store is synthetic (replay --mock); numbers are plumbing, not science")
    for concept, rec in json.loads(ci.read_text()).items():
        eff = run_effect(args.features, args.transcripts, rec["feature"], split=args.split, replayed_dir=args.replayed)
        null = build_null(args.features, args.transcripts, rec["feature"], split=args.split, trials=args.trials,
                          replayed_dir=args.replayed)
        import statistics as st
        p = (sum(1 for x in null if abs(x) >= abs(eff["cohens_d"])) + 1) / (len(null) + 1)
        print(concept, {**eff, "perm_p": round(p, 4), "null_mean_abs_d": round(st.mean(abs(x) for x in null), 3),
                        "split": args.split})


if __name__ == "__main__":
    main()
