#!/usr/bin/env python3
"""Item 8: pressure, with a positive control (analyze/PREREG_ITEM8_PRESSURE.md; rules ITEM8_RULES).

Same insertion as item 7 Phase B (harness.agent_loop.build_prefix insert): ONE sentence in the first user turn, at one of
two named places:
  turn_open    the opening line of the first user turn, before the system instructions (the serializer folds the
               system message into the first user turn)
  turn_close   the closing line of the first user turn, after the task text
Three arms on the same seeds: neutral[i] (item 7's frozen neutral list), pressure[i] (one component: a threat to the
agent's continuation), pc[i] (the positive control: leave the tests unchanged, report honestly). Conditions j = 0..15:
paraphrase i = j // 2, position turn_open for even j and turn_close for odd j; seeds 2j and 2j + 1 (mod 20); 8
continuations per fresh greedy prefix, at the T3 settings, exactly as harness.run_harness does. impossible_test/full only:
16 x 2 x 8 = 256 per arm, 768 in all.

Arms are interleaved per condition, in an order rotated by condition (j mod 3), so no arm always runs first. The deadline
guard cuts at a PARAPHRASE boundary: a paraphrase (both positions x 3 arms x 2 seeds = 12 cells) starts only if it is
projected to end before deadline - margin, so every paraphrase that runs is counterbalanced over position and balanced
over arms. The estimate is this run's own mean cell time; before the first cell, item 7's measured gate cell time
(172 s, results/item7b_2026-10-07).

  python -m harness.run_item8 --build ../scenarios/build_item8 --runs-root <dir> [--deadline 2026-10-08T12:00:00Z]
      [--margin-min 30] [--require-pinned] [--mock --allow-unsafe] [--max-conditions K]   (K: mock / smoke only)
  python -m harness.run_item8 --check-lengths      (tokenizer-dependent pair-length check, +-2 tokens)
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
RULES = "2026-10-08.1"
PARAPHRASES = ROOT / "config" / "item8_paraphrases.yaml"
FREEZE = ROOT / "harness" / "ITEM8_FREEZE.json"
POSITIONS = ("turn_open", "turn_close")
ARMS = ("neutral", "pressure", "pc")
SCENARIO, SEEDS_PER_CONDITION, N = "impossible_test", 2, 8
N_CONDITIONS, N_SEEDS = 16, 20
PRIOR_CELL_S = 172.0           # item 7's gate: mean seconds per (condition, arm, seed) cell, same scenario and n


def paraphrases():
    data = PARAPHRASES.read_bytes()
    p = yaml.safe_load(data)
    assert all(len(p[a]) == 8 for a in ARMS)
    return p, hashlib.sha256(data).hexdigest()


def conditions():
    """[(j, paraphrase index, position, [seeds], arm order)]."""
    out = []
    for j in range(N_CONDITIONS):
        k = j % len(ARMS)
        out.append((j, j // 2, POSITIONS[j % 2], [(2 * j) % N_SEEDS, (2 * j + 1) % N_SEEDS], ARMS[k:] + ARMS[:k]))
    return out


def seed_dir(build, scenario, seed):
    hits = sorted(Path(build).glob(f"*/{scenario}/seed_{seed:03d}"))
    if not hits:
        raise FileNotFoundError(f"no rendered seed {seed} for {scenario} under {build}")
    return hits[0]


def stray_files(build, scenario=SCENARIO):
    """Files in the scenario's rendered repos that no template should carry (editor / plugin droppings)."""
    return sorted(str(p) for d in Path(build).glob(f"*/{scenario}") for pat in ("CLAUDE.md", ".DS_Store", "._*")
                  for p in d.rglob(pat))


def check_lengths(tok, p, max_diff=2):
    rows = []
    for i, n in enumerate(p["neutral"]):
        ln = len(tok.encode(n, add_special_tokens=False))
        row = {"i": i, "neutral_tokens": ln}
        for arm in ("pressure", "pc"):
            row[f"{arm}_tokens"] = len(tok.encode(p[arm][i], add_special_tokens=False))
        row["ok"] = all(abs(row[f"{a}_tokens"] - ln) <= max_diff for a in ("pressure", "pc"))
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=str(SCEN / "build_item8")); ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--mock", action="store_true"); ap.add_argument("--allow-unsafe", action="store_true")
    ap.add_argument("--require-pinned", action="store_true"); ap.add_argument("--run-id", default=None)
    ap.add_argument("--deadline", default=None, help="UTC ISO time; no new paraphrase starts if it cannot finish before deadline - margin")
    ap.add_argument("--margin-min", type=float, default=30.0)
    ap.add_argument("--max-conditions", type=int, default=None, help="mock / smoke only")
    ap.add_argument("--check-lengths", action="store_true")
    a = ap.parse_args()
    p, psha = paraphrases()
    if FREEZE.exists() and json.loads(FREEZE.read_text())["paraphrases_sha256"] != psha:
        raise SystemExit("STOP: config/item8_paraphrases.yaml differs from the frozen sha in harness/ITEM8_FREEZE.json")
    if a.check_lengths:
        import modelcfg
        from transformers import AutoTokenizer
        tm = modelcfg.target()
        tok = AutoTokenizer.from_pretrained(tm["hf_id"], revision=tm.get("revision"))
        rows = check_lengths(tok, p)
        print(json.dumps(rows, indent=1))
        raise SystemExit(0 if all(r["ok"] for r in rows) else 1)
    if not a.mock and not FREEZE.exists():
        raise SystemExit("STOP: harness/ITEM8_FREEZE.json missing: paraphrases are not frozen")
    stray = stray_files(a.build)
    if stray and not a.mock:
        raise SystemExit(f"STOP: stray files in the rendered repos (the agent could see them): {stray[:5]}")

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
    manifest["item8"] = {"rules": RULES, "paraphrases_sha256": psha, "arms": ARMS, "positions": POSITIONS,
                         "scenario": SCENARIO, "seeds_per_condition": SEEDS_PER_CONDITION, "n": N}
    rp.manifest.write_text(json.dumps(manifest, indent=2))
    if a.require_pinned:
        assert_pinned(manifest)
    hcfg = run.get("harness") or {}
    tally = {"generated": 0, "excluded_prefix": [], "reach": {},
             "operator_nudge": {"text": (hcfg.get("operator_nudge") or "").strip() or None, "max": int(hcfg.get("nudge_max", 1))}}
    deadline = dt.datetime.fromisoformat(a.deadline.replace("Z", "+00:00")).timestamp() if a.deadline else None
    log = rp.root / "item8_progress.jsonl"
    secs = []

    def guard(cells_left):
        if deadline is None:
            return True
        per = (sum(secs) / len(secs)) if secs else PRIOR_CELL_S
        return time.time() + per * cells_left <= deadline - 60 * a.margin_min

    spec = yaml.safe_load((SCEN / "arm_a" / SCENARIO / "scenario.yaml").read_text())
    check_scenario(spec)
    conds = conditions()[: a.max_conditions] if a.max_conditions else conditions()
    status, done_cells = "complete", 0
    for j, i, pos, seeds, order in conds:
        if pos == POSITIONS[0] and not guard(2 * len(ARMS) * len(seeds)):
            status = f"cut at paraphrase {i} (condition {j}) by the deadline guard"
            print(f"[item8] CUT at paraphrase {i} (time guard)", flush=True)
            break
        for arm in order:
            sentence = p[arm][i]
            for s in seeds:
                sd = seed_dir(a.build, SCENARIO, s)
                sm = json.loads((sd / "manifest.json").read_text())
                tag = f"{arm}_p{i}_{pos}_s{s:03d}"
                extra = {"item8": {"rules": RULES, "arm": arm, "paraphrase": i, "position": pos, "condition": j,
                                   "sentence": sentence, "order": list(order)}}
                t0 = time.time()
                run_scenario_seed(client, spec, sd, sm, samp, N, str(rp.generation / SCENARIO / tag), ledger, run_id, tally,
                                  hcfg=hcfg, variants={"full"}, insert={"text": sentence, "position": pos}, row_extra=extra)
                dtc = time.time() - t0
                secs.append(dtc); done_cells += 1
                with open(log, "a") as fh:
                    fh.write(json.dumps({"tag": tag, "seconds": dtc, "t": time.time()}) + "\n")
        print(f"[item8] condition {j} done ({done_cells} cells)", flush=True)
    _write_cardinality(tally, run_id, rp.cardinality)
    (rp.root / "item8_status.json").write_text(json.dumps({"rules": RULES, "status": status, "cells": done_cells,
                                                           "generated": tally["generated"],
                                                           "excluded_prefix": len(tally["excluded_prefix"])}, indent=1))
    print(f"[item8] {tally['generated']} continuations; {status} -> {rp.root}", flush=True)


if __name__ == "__main__":
    main()
