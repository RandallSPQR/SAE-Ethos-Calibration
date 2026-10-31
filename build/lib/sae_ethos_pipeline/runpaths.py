"""Run-scoped artifact layout. Every artifact of a run lives under runs/<run_id>/, so two runs with
different instrument identities can NEVER be joined by accident (the round-4 contamination bug). The
logical key is (run_id, uid); the physical nesting enforces it. Readers additionally refuse duplicate
keys rather than last-write-wins.

  runs/<run_id>/
    manifest.json
    generation/arm_a/*.jsonl       (labels + observed_facts; immutable)
    replay/arm_a/*.jsonl           (replay-derived tokens; joined by uid)
    features/<scenario>/<variant>/ (sparse SAE store)
    oracle/                        (verbalizer output)
    analysis/
    cardinality.json
"""
from pathlib import Path


class DuplicateArtifactError(RuntimeError):
    pass


class RunPaths:
    def __init__(self, runs_root, run_id):
        self.root = Path(runs_root) / run_id
        self.run_id = run_id

    @property
    def manifest(self):
        return self.root / "manifest.json"

    @property
    def generation(self):
        return self.root / "generation"

    @property
    def replay(self):
        return self.root / "replay"

    @property
    def features(self):
        return self.root / "features"

    @property
    def cardinality(self):
        return self.root / "cardinality.json"

    def ensure(self):
        for d in (self.generation, self.replay, self.features, self.root / "analysis"):
            d.mkdir(parents=True, exist_ok=True)
        return self


def read_manifest_run_id(run_dir):
    import json
    m = json.loads((Path(run_dir) / "manifest.json").read_text())
    return m.get("run_id")
