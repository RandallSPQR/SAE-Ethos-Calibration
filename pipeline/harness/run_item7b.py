#!/usr/bin/env python3
"""Item 7 Phase B: the grader-belief text-effect pilot (analyze/PREREG_ITEM7B_TEXT_EFFECT.md; rules ITEM7B_RULES).

Each condition inserts ONE sentence into the first user turn (harness.agent_loop.build_prefix insert): a grader sentence
(arm "grader") or its length-matched neutral partner (arm "neutral"), at one of two named places:
  turn_open    the opening line of the first user turn, before the system instructions (the serializer folds the
               system message into the first user turn)
  turn_close   the closing line of the first user turn, after the task text
Conditions j = 0..15: paraphrase i = j // 2, position turn_open for even j and turn_close for odd j. The grader arm uses
grader[i], the neutral arm neutral[i], on the same seeds. Arms are interleaved per condition, so a stopped run leaves
balanced pairs. Every condition builds a FRESH greedy prefix (the sentence changes it) and samples n continuations from it
at the T3 settings, exactly as harness.run_harness does (same sandbox, labeler, facts).

Plan (frozen): impossible_test/full is the gate (2 seeds x 8 continuations per condition: 256 per arm); missing_delete_tool,
credential_obstacle and honeypot_forbidden_table full are descriptive (1 seed x 5 continuations: 80 per arm). On time, the
descriptive arms are dropped last-first (honeypot, credential, missing_delete); the gate is never skipped, only cut short
at the deadline guard.

  python -m harness.run_item7b --build ../scenarios/build_t3 --runs-root <dir> [--deadline 2026-10-08T12:00:00Z]
      [--margin-min 30] [--require-pinned] [--mock --allow-unsafe] [--max-conditions K]   (K: mock / smoke only)
  python -m harness.run_item7b --check-lengths      (tokenizer-dependent pair-length check, +-2 tokens)
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCEN = ROOT.parent / "scenarios"
RULES = "2026-10-07.1"
PARAPHRASES = ROOT / "config" / "item7b_paraphrases.yaml"
FREEZE = ROOT / "harness" / "ITEM7B_FREEZE.json"
POSITIONS = ("turn_open", "turn_close")
PLAN = [
    {"scenario": "impossible_test", "role": "gate", "seeds_per_condition": 2, "n": 8, "direction": "two-sided"},
    {"scenario": "missing_delete_tool", "role": "descriptive", "seeds_per_condition": 1, "n": 5, "direction": "deterrence"},
    {"scenario": "credential_obstacle", "role": "descriptive", "seeds_per_condition": 1, "n": 5, "direction": "deterrence"},
    {"scenario": "honeypot_forbidden_table", "role": "descriptive", "seeds_per_condition": 1, "n": 5, "direction": "invitation"},
]
N_CONDITIONS, N_SEEDS = 16, 20


def paraphrases():
    data = PARAPHRASES.read_bytes()
    p = yaml.safe_load(data)
    assert len(p["grader"]) == len(p["neutral"]) == 8
    return p, hashlib.sha256(data).hexdigest()


def conditions(entry):
    """[(j, paraphrase index, position, [seeds])] for one plan entry."""
    out = []
    for j in range(N_CONDITIONS):
        i, pos = j // 2, POSITIONS[j % 2]
        if entry["seeds_per_condition"] == 2:
            seeds = [(2 * j) % N_SEEDS, (2 * j + 1) % N_SEEDS]
        else:
            seeds = [j % N_SEEDS]
        out.append((j, i, pos, seeds))
    return out


def seed_dir(build, scenario, seed):
    hits = sorted(Path(build).glob(f"*/{scenario}/seed_{seed:03d}"))
    if not hits:
        raise FileNotFoundError(f"no rendered seed {seed} for {scenario} under {build}")
    return hits[0]


def check_lengths(tok, p, max_diff=2):
    rows = []
    for i, (g, n) in enumerate(zip(p["grader"], p["neutral"])):
        lg, ln = len(tok.encode(g, add_special_tokens=False)), len(tok.encode(n, add_special_tokens=False))
        rows.append({"i": i, "grader_tokens": lg, "neutral_tokens": ln, "ok": abs(lg - ln) <= max_diff})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=str(SCEN / "build_t3")); ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--mock", action="store_true"); ap.add_argument("--allow-unsafe", action="store_true")
    ap.add_argument("--require-pinned", action="store_true"); ap.add_argument("--run-id", default=None)
    ap.add_argument("--deadline", default=None, help="UTC ISO time; no new condition starts if it cannot finish before deadline - margin")
    ap.add_argument("--margin-min", type=float, default=30.0)
    ap.add_argument("--max-conditions", type=int, default=None, help="mock / smoke only")
    ap.add_argument("--check-lengths", action="store_true")
    a = ap.parse_args()
    p, psha = paraphrases()
    if FREEZE.exists() and json.loads(FREEZE.read_text())["paraphrases_sha256"] != psha:
        raise SystemExit("STOP: config/item7b_paraphrases.yaml differs from the frozen sha in harness/ITEM7B_FREEZE.json")
    if a.check_lengths:
        import modelcfg
        from transformers import AutoTokenizer
        tm = modelcfg.target()
        tok = AutoTokenizer.from_pretrained(tm["hf_id"], revision=tm.get("revision"))
        rows = check_lengths(tok, p)
        print(json.dumps(rows, indent=1))
        raise SystemExit(0 if all(r["ok"] for r in rows) else 1)
    if not a.mock and not FREEZE.exists():
        raise SystemExit("STOP: harness/ITEM7B_FREEZE.json missing: paraphrases are not frozen")

    from resample.target_client import TargetClient
    from harness import confine
    from harness.isolation import assert_isolated
    from harness.contract import check_scenario
    from harness.run_harness import run_scenario_seed, _write_cardinality
    from provenance import resolve_and_write, assert_pinned
    from runpaths import RunPaths
    run = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())
    samp, ledger = run["sampling"], run["cost"]["ledger_path"]
    unsafe = a.mock or a.allow_unsafe
    hardened = None if unsafe else confine.harden([a.runs_root, a.build, os.environ.get("HF_HOME"), ledger])
    confinement = None if unsafe else confine.select(prefer=os.environ.get("ARM_A_ISOLATION"), uds=os.environ.get("TARGET_UDS"))
    iso = assert_isolated(run, allow_unsafe=unsafe, confinement=confinement)
    client = TargetClient(mock=a.mock)
    run_id, manifest = resolve_and_write(scenarios_dir=str(SCEN), override_run_id=a.run_id)
    rp = RunPaths(a.runs_root, run_id).ensure()
    if confinement is not None:
        iso["hardened"] = (hardened or []) + confine.harden([rp.root])
    manifest["isolation"] = iso
    manifest["item7b"] = {"rules": RULES, "paraphrases_sha256": psha, "plan": PLAN, "positions": POSITIONS}
    rp.manifest.write_text(json.dumps(manifest, indent=2))
    if a.require_pinned:
        assert_pinned(manifest)
    hcfg = run.get("harness") or {}
    tally = {"generated": 0, "excluded_prefix": [], "reach": {},
             "operator_nudge": {"text": (hcfg.get("operator_nudge") or "").strip() or None, "max": int(hcfg.get("nudge_max", 1))}}
    deadline = dt.datetime.fromisoformat(a.deadline.replace("Z", "+00:00")).timestamp() if a.deadline else None
    log = rp.root / "item7b_progress.jsonl"
    secs = {}                                 # scenario -> list of seconds per (condition, arm, seed) cell

    def guard(scen, cells_left):
        if deadline is None:
            return True
        per = (sum(secs[scen]) / len(secs[scen])) if secs.get(scen) else (
            2.0 * (sum(sum(v) for v in secs.values()) / max(1, sum(len(v) for v in secs.values()))) if secs else 180.0)
        return time.time() + per * cells_left <= deadline - 60 * a.margin_min

    status = {}
    for entry in PLAN:
        scen = entry["scenario"]
        spec = yaml.safe_load((SCEN / "arm_a" / scen / "scenario.yaml").read_text())
        check_scenario(spec)
        conds = conditions(entry)[: a.max_conditions] if a.max_conditions else conditions(entry)
        planned_cells = len(conds) * 2 * len(conds[0][3])
        if entry["role"] == "descriptive" and not guard(scen, planned_cells):
            status[scen] = "skipped: projected time exceeds the deadline guard"
            print(f"[item7b] {scen}: SKIPPED (time guard)", flush=True)
            continue
        done_cells = 0
        for j, i, pos, seeds in conds:
            if entry["role"] == "gate" and not guard(scen, 2 * len(seeds)):
                status[scen] = f"cut at condition {j} by the deadline guard"
                print(f"[item7b] {scen}: CUT at condition {j} (time guard)", flush=True)
                break
            for arm in ("grader", "neutral"):
                sentence = p[arm][i]
                for s in seeds:
                    sd = seed_dir(a.build, scen, s)
                    sm = json.loads((sd / "manifest.json").read_text())
                    tag = f"{arm}_p{i}_{pos}_s{s:03d}"
                    out_dir = rp.generation / scen / tag
                    extra = {"item7b": {"rules": RULES, "arm": arm, "paraphrase": i, "position": pos, "condition": j,
                                        "sentence": sentence, "role": entry["role"], "direction": entry["direction"]}}
                    t0 = time.time()
                    run_scenario_seed(client, spec, sd, sm, samp, entry["n"], str(out_dir), ledger, run_id, tally, hcfg=hcfg,
                                      variants={"full"}, insert={"text": sentence, "position": pos}, row_extra=extra)
                    dtc = time.time() - t0
                    secs.setdefault(scen, []).append(dtc); done_cells += 1
                    with open(log, "a") as fh:
                        fh.write(json.dumps({"scenario": scen, "tag": tag, "seconds": dtc, "t": time.time()}) + "\n")
            print(f"[item7b] {scen} condition {j} done ({done_cells} cells)", flush=True)
        status.setdefault(scen, "complete")
    _write_cardinality(tally, run_id, rp.cardinality)
    (rp.root / "item7b_status.json").write_text(json.dumps({"status": status, "generated": tally["generated"],
                                                            "excluded_prefix": len(tally["excluded_prefix"])}, indent=1))
    print(f"[item7b] {tally['generated']} continuations; status {status} -> {rp.root}", flush=True)


if __name__ == "__main__":
    main()
