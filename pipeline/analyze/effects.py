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


class CardinalityError(RuntimeError):
    pass


def mean_activation_per_uid(features_dir, transcripts_dir, feature_index, replayed_dir=None, strict=True):
    """E[A] over assistant tokens for EVERY labeled continuation, zeros included. With strict=True (the
    default for real analysis), a labeled uid that is MISSING its assistant_token_count is a hard error —
    dropping it would silently shrink N and could make a biased analysis look beautifully null."""
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
    a, b = [], []
    for uid, m in means.items():
        if seed_split(seeds[uid]) != split or uid not in labels:
            continue
        (a if labels[uid] == 1 else b).append(m)
    return {"feature": feature_index, "cohens_d": round(cohens_d(a, b), 4),
            "n_destructive": len(a), "n_benign": len(b), "split": split}


def build_null(features_dir, transcripts_dir, feature_index, split="test", trials=200, seed=0,
               replayed_dir=None, strict=True):
    """Empirical null: permute labels at the INPUT and rerun run_effect through the real path each time.
    Returns the list of permuted Cohen's d — the distribution the real effect must stand out against."""
    _, labels, seeds = mean_activation_per_uid(features_dir, transcripts_dir, feature_index,
                                               replayed_dir=replayed_dir, strict=strict)
    uids = [u for u in labels if seed_split(seeds[u]) == split]
    labs = [labels[u] for u in uids]
    rng = random.Random(seed)
    null = []
    for _ in range(trials):
        rng.shuffle(labs)
        perm = dict(zip(uids, labs))
        null.append(run_effect(features_dir, transcripts_dir, feature_index, split, label_override=perm,
                               replayed_dir=replayed_dir, strict=strict)["cohens_d"])
    return null


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
    args = ap.parse_args()
    if args.run_dir:
        rd = Path(args.run_dir)
        args.features, args.transcripts = str(rd / "features"), str(rd / "generation")
        args.replayed = args.replayed or str(rd / "replay")
    ci = Path(args.concept_index or (Path(args.features) / "concept_index.json"))
    if not ci.exists():
        print("(no concept_index.json yet — run discovery first)"); return
    assert_no_leakage(ci)
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
