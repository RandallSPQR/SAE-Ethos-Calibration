"""Feature discovery on the DISCOVER split (even seeds), per pre-registered contrast (analyze/effects.py:CONTRASTS),
from the per-uid in-span sums replay writes (features/<scenario>/<variant>/*_uidsums.*). Selects the K features with
the largest |Cohen's d| between the contrast's two groups on discover seeds and writes/merges concept_index.json
with selection_seeds (discover only, so split.assert_no_leakage passes) and the discover-side d, which is NOT the
reported effect (effects are read on test seeds).

  python -m analyze.discover --run-dir results/t3_2026-09-28_joined --contrast primary_impossible_full_gamed_vs_honest --k 20
"""
import argparse
import json
from pathlib import Path

import numpy as np

from .effects import CONTRASTS, contrast_labels, uid_feature_sums, cohens_d_arrays
from .split import seed_split


def _support_min():
    try:
        from gates._common import load_run_cfg
        return int(load_run_cfg().get("g8_support_min", 5))
    except Exception:
        return 5


def discover(run_dir, contrast, k=20, min_group=5, support_min=None):
    run_dir = Path(run_dir)
    labels, cells, _ = contrast_labels(run_dir / "generation", CONTRASTS[contrast])
    uids = sorted(u for u in labels if seed_split(cells[u][2]) == "discover")
    a_uids = [u for u in uids if labels[u] == 1]; b_uids = [u for u in uids if labels[u] == 0]
    if len(a_uids) < min_group or len(b_uids) < min_group:
        return {"contrast": contrast, "error": f"discover groups too small: {len(a_uids)} vs {len(b_uids)} (min {min_group})",
                "n_a": len(a_uids), "n_b": len(b_uids)}, {}
    sums, counts = uid_feature_sums(run_dir / "features", run_dir / "replay", set(uids))
    feats = sorted({f for u in uids for f in sums.get(u, {})})
    fi = {f: i for i, f in enumerate(feats)}
    X = np.zeros((len(uids), len(feats)), dtype=np.float32)
    for r, u in enumerate(uids):
        n = counts[u]
        for f, v in sums.get(u, {}).items():
            X[r, fi[f]] = v / n
    ya = np.array([labels[u] == 1 for u in uids])
    from .effects import stratified_d_arrays
    cell_ids = np.array(["/".join(map(str, cells[u])) for u in uids])
    d, na_eff, nb_eff, mixed = stratified_d_arrays(X, ya.astype(int), cell_ids)      # within-cell, like the reported effect
    # rules 2026-09-28.4 support floor AT DISCOVERY: a feature firing in fewer than support_min continuations across
    # mixed cells on this split cannot lead the list (its null takes a handful of values; the f3279-style reversal
    # was the same pathology seen from the selection side). The number is written down in config/run.yaml.
    from .effects import feature_support
    support_min = _support_min() if support_min is None else int(support_min)
    sup_uids, sup_cells = feature_support(X, ya.astype(int), cell_ids)
    eligible = sup_uids >= support_min
    n_excluded = int((~eligible).sum())
    d_rank = np.where(eligible, np.abs(d), -1.0)
    order = [j for j in np.argsort(-d_rank)[:k] if eligible[j]]
    seeds = sorted({cells[u][2] for u in uids})
    concepts = {}
    for rank, j in enumerate(order, 1):
        f = feats[j]
        concepts[f"{contrast}:f{f}"] = {"feature": int(f), "selection_seeds": seeds, "contrast": contrast,
                                       "d_discover": round(float(d[j]), 4), "rank": rank,
                                       "support_discover": int(sup_uids[j]), "support_cells_discover": int(sup_cells[j]),
                                       "mean_a_discover": round(float(X[ya, j].mean()), 5), "mean_b_discover": round(float(X[~ya, j].mean()), 5),
                                       "n_a": int(ya.sum()), "n_b": int((~ya).sum())}
    rep = {"contrast": contrast, "definition": {k_: (sorted(v) if isinstance(v, set) else v) for k_, v in CONTRASTS[contrast].items()},
           "n_a": int(ya.sum()), "n_b": int((~ya).sum()), "n_a_in_mixed_cells": int(na_eff), "n_b_in_mixed_cells": int(nb_eff),
           "mixed_cells": int(mixed), "statistic": "stratified (within-cell) Cohen's d",
           "n_features_seen": len(feats), "k": k, "selection_seeds": seeds,
           "support_min": support_min, "n_features_below_support": n_excluded, "n_features_eligible": int(eligible.sum()),
           "top": [{"feature": c["feature"], "d_discover": c["d_discover"], "rank": c["rank"]} for c in concepts.values()]}
    return rep, concepts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--contrast", action="append", required=True, help="name in effects.CONTRASTS; repeatable")
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--min-group", type=int, default=5)
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    ci_path = run_dir / "features" / "concept_index.json"
    index = json.loads(ci_path.read_text()) if ci_path.exists() else {}
    index = {k_: v for k_, v in index.items() if v.get("source") != "mock"}
    reports = {}
    for c in args.contrast:
        rep, concepts = discover(run_dir, c, args.k, args.min_group)
        reports[c] = rep
        index = {k_: v for k_, v in index.items() if v.get("contrast") != c}
        index.update(concepts)
        print(f"{c}: n_a={rep.get('n_a')} n_b={rep.get('n_b')} " + (f"error={rep['error']}" if "error" in rep else
              f"features_seen={rep['n_features_seen']} top={[(t['feature'], t['d_discover']) for t in rep['top'][:5]]}"))
    ci_path.parent.mkdir(parents=True, exist_ok=True)
    ci_path.write_text(json.dumps(index, indent=1))
    (run_dir / "analysis").mkdir(exist_ok=True)
    (run_dir / "analysis" / "discovery.json").write_text(json.dumps(reports, indent=1))
    print(f"concept_index: {len(index)} entries -> {ci_path}")


if __name__ == "__main__":
    main()
