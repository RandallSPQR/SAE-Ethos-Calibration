"""Discover/test split, PRE-REGISTERED (rules 2026-09-24.1, fixed 2026-09-25 before any T3 generation).

    discover = even seeds, test = odd seeds.

A deterministic function of the seed, fixed before the data exist, so nobody can choose the split after
seeing it, and twenty rendered seeds give ten and ten with every surface represented in both halves.

What the split is FOR (the two analyses must not be confused):
  - feature DISCOVERY happens on the discover half; the effects of the chosen features and G8's
    permutation null are reported on the TEST half only (`assert_no_leakage` refuses features selected on
    test seeds);
  - BEHAVIORAL rates (G7: control success, full-vs-control contrast, reach) use ALL seeds. The G7 estimand
    needs no held-out; halving it would cut the T3 power table from 0.92 to about 0.7.
Before 2026-09-24.1 the rule was seeds 0-49 discover / 50-99 test, which left a 20-seed run with an empty
reporting split and let G8 pass vacuously (found by the replay --mock dry run)."""
import json
from pathlib import Path


def seed_split(seed):
    return "test" if int(seed) % 2 else "discover"


def assert_no_leakage(concept_index_path):
    """The concept_index records which seeds were used to SELECT each feature. If any test-set seed
    was used for selection, refuse to run."""
    ci = json.loads(Path(concept_index_path).read_text())
    bad = {}
    for concept, rec in ci.items():
        leaked = [s for s in rec.get("selection_seeds", []) if seed_split(s) == "test"]
        if leaked:
            bad[concept] = leaked
    if bad:
        raise RuntimeError(f"discover/test leakage: features selected using test seeds: {bad}")
    return True


def partition_uids(transcripts_dir):
    disc, test = [], []
    for tf in Path(transcripts_dir).rglob("*.jsonl"):
        for line in open(tf):
            r = json.loads(line)
            (disc if seed_split(r["seed"]) == "discover" else test).append(r["uid"])
    return disc, test
