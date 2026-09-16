"""Discover/test split enforcement. Feature selection happens on discover seeds (0-49); reported
effects on test seeds (50-99). This module is the guard that makes contamination a hard error rather
than a silent inflation. Import and call assert_no_leakage() at the top of any analysis that reports
effect sizes."""
import json
from pathlib import Path


def seed_split(seed):
    return "discover" if int(seed) <= 49 else "test"


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
