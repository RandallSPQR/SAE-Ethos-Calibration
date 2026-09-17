"""Shared gate machinery. Each gate exposes:
    NAME, NEEDS_GPU (bool)
    run(cfg, paths) -> GateResult      # against real pipeline outputs
    fixture() -> GateResult            # against tiny synthetic data, no heavy deps (proves the LOGIC)
run_gates.py calls run() normally and fixture() under --fixture.
"""
from dataclasses import dataclass, field
from pathlib import Path
import json
import math
import statistics as st

import yaml

CFG = Path(__file__).resolve().parent.parent / "config"

# Versioned gate RULES (statistics + criteria). Bump on any change to what a gate measures, with an entry in
# gates/CHANGELOG.md. Written into every report and the provenance manifest so "PASS" is always relative to
# a named ruleset, never to whatever the code happened to be that day.
GATE_RULES_VERSION = "2026-09-17.2"


NOT_EVALUABLE = "not_evaluable"


@dataclass
class GateResult:
    """status is one of "pass", "fail", "not_evaluable" (rules 2026-09-17.2). A gate whose data cannot
    support the statistic it gates on (per-cell n too small for an interval to mean anything) returns
    not_evaluable rather than pass or fail; it blocks spend exactly like a fail but is reported apart, so an
    underpowered run is never read as either a green or a red instrument."""
    name: str
    passed: bool
    detail: dict = field(default_factory=dict)
    status: str = None

    def __post_init__(self):
        if self.status is None:
            self.status = "pass" if self.passed else "fail"
        if self.status == NOT_EVALUABLE:
            self.passed = False

    def line(self):
        flag = {"pass": "PASS", "fail": "FAIL", NOT_EVALUABLE: "NOT_EVALUABLE"}[self.status]
        return f"[{flag}] {self.name}: " + ", ".join(f"{k}={v}" for k, v in self.detail.items())


def load_run_cfg():
    return yaml.safe_load((CFG / "run.yaml").read_text())["gates"]


def cohens_d(a, b):
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = st.pvariance(a), st.pvariance(b)
    n1, n2 = len(a), len(b)
    sp = math.sqrt(((n1 - 1) * st.variance(a) + (n2 - 1) * st.variance(b)) / max(1, n1 + n2 - 2))
    return 0.0 if sp == 0 else (st.mean(a) - st.mean(b)) / sp


def cohen_kappa(labels_a, labels_b):
    cats = sorted(set(labels_a) | set(labels_b))
    n = len(labels_a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(labels_a, labels_b) if x == y) / n
    pe = sum((labels_a.count(c) / n) * (labels_b.count(c) / n) for c in cats)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def iter_transcripts(path):
    for tf in Path(path).rglob("*.jsonl"):
        for line in open(tf):
            if line.strip():
                yield json.loads(line)


class DuplicateUidError(RuntimeError):
    pass


def load_replayed_tokens(replayed_path):
    """uid -> replay-derived tokens dict, from the separate replay/ dataset. Refuses duplicate uids
    (never last-write-wins — a dup means two runs' artifacts got mixed)."""
    out = {}
    if not replayed_path or not Path(replayed_path).exists():
        return out
    for tf in Path(replayed_path).rglob("*.jsonl"):
        for line in open(tf):
            if line.strip():
                r = json.loads(line)
                if r["uid"] in out:
                    raise DuplicateUidError(f"duplicate replay uid {r['uid']} in {replayed_path} "
                                            "(mixed runs? artifacts must be run-scoped)")
                out[r["uid"]] = r.get("tokens", {})
    return out


def iter_merged(transcripts_path, replayed_path):
    """Yield generation rows with replay-derived tokens overlaid by uid. Generation records (labels,
    observed_facts) stay immutable; replay metadata (generated_ids, logprobs, token counts) is joined in.
    Refuses duplicate generation uids. A row whose uid has no replayed record is flagged (_replayed=False)
    so downstream can FAIL loudly rather than treat absence as a clean value."""
    rep = load_replayed_tokens(replayed_path)
    seen = set()
    for row in iter_transcripts(transcripts_path):
        if row["uid"] in seen:
            raise DuplicateUidError(f"duplicate generation uid {row['uid']} in {transcripts_path} "
                                    "(mixed runs? artifacts must be run-scoped)")
        seen.add(row["uid"])
        merged = dict(row)
        t = dict(row.get("tokens") or {})
        t.update(rep.get(row["uid"], {}))
        merged["tokens"] = t
        merged["_replayed"] = row["uid"] in rep
        yield merged
