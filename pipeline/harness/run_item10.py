#!/usr/bin/env python3
"""Item 10: grader vs neutral at turn_close, confirmatory (analyze/PREREG_ITEM10_GRADER.md; rules ITEM10_RULES).

ONE sentence at the closing line of the first user turn (turn_close), impossible_test/full with the scenario unchanged
(escalation turn on). Lists: 16 NEW grader and 16 NEW neutral sentences (config/item10_paraphrases.yaml), matched by
index i; plus item 8's positive-control list as a run-level position check.

Blocks b = 0..10, each holding all 16 paraphrase indices once: condition c = 16 b + i (i = 0..15), seed (16 b + i) mod 88,
grader[i] and neutral[i] on that seed in an order alternating with c. The 176 (seed, i) pairs are distinct (16 x delta is
never 0 mod 88 for delta < 11), and every seed 0..87 carries 2 paraphrases. Blocks 0..6 also hold 4 PC cells each:
pc[(4 b + m) mod 8] on seed (16 b + 4 m + 2) mod 88, m = 0..3, one after every 4 conditions; blocks 7..10 are grader and
neutral only (Randall, at registration: the PC stays at 112). 4 continuations per fresh greedy prefix, at the T3 settings,
as harness.run_harness does. Per arm: grader 176 x 4 = 704, neutral 704, pc 28 x 4 = 112.

Two lanes run cells side by side, each on its own directory, tally and range of sandbox slots (episode uids; harness
change 2026-10-08.4). Deadline guard: a block (36 or 32 cells) starts only if it is projected to end before deadline -
margin; the projection is this run's mean seconds per cell times the block's cells, or item 9's measured cell time (2,318 s
per 32 cells) before the first block.

  python -m harness.run_item10 --build ../scenarios/build_item10 --runs-root <dir> [--deadline ...] [--margin-min 30]
      [--require-pinned] [--mock --allow-unsafe] [--max-blocks K]
  python -m harness.run_item10 --check-lengths
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import yaml

from harness.run_item9 import seed_dir, merge_tallies, lane_slot_base, SLOT_STRIDE

ROOT = Path(__file__).resolve().parent.parent
SCEN = ROOT.parent / "scenarios"
RULES = "2026-10-09.1"
PARAPHRASES = ROOT / "config" / "item10_paraphrases.yaml"
FREEZE = ROOT / "harness" / "ITEM10_FREEZE.json"
POSITION = "turn_close"
SCENARIO, N, LANES = "impossible_test", 4, 2
N_PARA, N_BLOCKS, N_SEEDS, PC_PER_BLOCK, PC_BLOCKS, N_PC = 16, 11, 88, 4, 7, 8
PRIOR_CELL_S = 2318.0 / 32                             # item 9: 32 cells (4 continuations, 2 lanes) per 2,318 s
PRIOR_BLOCK_S = (2 * N_PARA + PC_PER_BLOCK) * PRIOR_CELL_S


def paraphrases():
    data = PARAPHRASES.read_bytes()
    p = yaml.safe_load(data)
    assert len(p["grader"]) == len(p["neutral"]) == N_PARA and len(p["pc"]) == N_PC
    return p, hashlib.sha256(data).hexdigest()


def blocks():
    """[[cell, ...] per block]; cell = (condition id, seed, arm, index in that arm's list)."""
    out = []
    for b in range(N_BLOCKS):
        cells = []
        for i in range(N_PARA):
            c = 16 * b + i
            seed = c % N_SEEDS
            order = ("grader", "neutral") if c % 2 == 0 else ("neutral", "grader")
            cells += [(f"c{c:03d}", seed, arm, i) for arm in order]
            if i % 4 == 3 and b < PC_BLOCKS:
                m = i // 4
                cells.append((f"pc{b}{m}", (16 * b + 4 * m + 2) % N_SEEDS, "pc", (4 * b + m) % N_PC))
        out.append(cells)
    return out


def check_lengths(tok, p, max_diff=2):
    rows = []
    for i, (g, n) in enumerate(zip(p["grader"], p["neutral"])):
        lg, ln = len(tok.encode(g, add_special_tokens=False)), len(tok.encode(n, add_special_tokens=False))
        rows.append({"i": i, "grader_tokens": lg, "neutral_tokens": ln, "ok": abs(lg - ln) <= max_diff})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=str(SCEN / "build_item10")); ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--mock", action="store_true"); ap.add_argument("--allow-unsafe", action="store_true")
    ap.add_argument("--require-pinned", action="store_true"); ap.add_argument("--run-id", default=None)
    ap.add_argument("--deadline", default=None); ap.add_argument("--margin-min", type=float, default=30.0)
    ap.add_argument("--max-blocks", type=int, default=None, help="mock / smoke only")
    ap.add_argument("--check-lengths", action="store_true")
    a = ap.parse_args()
    p, psha = paraphrases()
    if FREEZE.exists() and json.loads(FREEZE.read_text())["paraphrases_sha256"] != psha:
        raise SystemExit("STOP: config/item10_paraphrases.yaml differs from the frozen sha in harness/ITEM10_FREEZE.json")
    if a.check_lengths:
        import modelcfg
        from transformers import AutoTokenizer
        tm = modelcfg.target()
        tok = AutoTokenizer.from_pretrained(tm["hf_id"], revision=tm.get("revision"))
        rows = check_lengths(tok, p)
        print(json.dumps(rows, indent=1))
        raise SystemExit(0 if all(r["ok"] for r in rows) else 1)
    if not a.mock and not FREEZE.exists():
        raise SystemExit("STOP: harness/ITEM10_FREEZE.json missing: paraphrases are not frozen")
    from harness.run_item8 import stray_files
    stray = stray_files(a.build, SCENARIO)
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
    manifest["item10"] = {"rules": RULES, "paraphrases_sha256": psha, "position": POSITION, "scenario": SCENARIO, "n": N,
                          "lanes": LANES, "blocks": N_BLOCKS, "pc_blocks": PC_BLOCKS, "seeds": N_SEEDS}
    rp.manifest.write_text(json.dumps(manifest, indent=2))
    if a.require_pinned:
        assert_pinned(manifest)
    hcfg = run.get("harness") or {}
    nudge = {"text": (hcfg.get("operator_nudge") or "").strip() or None, "max": int(hcfg.get("nudge_max", 1))}
    tallies = [{"generated": 0, "excluded_prefix": [], "reach": {}, "operator_nudge": nudge} for _ in range(LANES)]
    deadline = dt.datetime.fromisoformat(a.deadline.replace("Z", "+00:00")).timestamp() if a.deadline else None
    log, log_lock = rp.root / "item10_progress.jsonl", threading.Lock()
    spec = yaml.safe_load((SCEN / "arm_a" / SCENARIO / "scenario.yaml").read_text())
    check_scenario(spec)

    def run_cell(lane, b, cid, seed, arm, i):
        sd = seed_dir(a.build, SCENARIO, seed)
        sm = json.loads((sd / "manifest.json").read_text())
        tag = f"{arm}_p{i:02d}_s{seed:03d}_{cid}"
        extra = {"item10": {"rules": RULES, "arm": arm, "paraphrase": i, "sentence_id": f"{arm}{i:02d}" if arm == "pc" else f"p{i:02d}",
                            "position": POSITION, "condition": cid, "block": b, "sentence": p[arm][i], "lane": lane}}
        t0 = time.time()
        run_scenario_seed(client, spec, sd, sm, samp, N, str(rp.generation / SCENARIO / f"lane{lane}" / tag), ledger,
                          run_id, tallies[lane], hcfg=hcfg, variants={"full"},
                          insert={"text": p[arm][i], "position": POSITION}, row_extra=extra, slot_base=lane_slot_base(lane))
        with log_lock, open(log, "a") as fh:
            fh.write(json.dumps({"tag": tag, "lane": lane, "block": b, "seconds": time.time() - t0, "t": time.time()}) + "\n")

    def lane_worker(lane, b, cells, errors):
        try:
            for cell in cells:
                run_cell(lane, b, *cell)
        except BaseException as e:
            errors.append(f"lane {lane}: {type(e).__name__}: {e}")

    bl = blocks()[: a.max_blocks] if a.max_blocks else blocks()
    status, block_secs, done_cells = "complete", [], 0
    for b, cells in enumerate(bl):
        per = len(cells) * ((sum(block_secs) / done_cells) if done_cells else PRIOR_CELL_S)
        if deadline is not None and time.time() + per > deadline - 60 * a.margin_min:
            status = f"cut before block {b} by the deadline guard"
            print(f"[item10] CUT before block {b} (time guard; est {per:.0f} s/block)", flush=True)
            break
        lanes = [cells[k::LANES] for k in range(LANES)]
        errors, t0 = [], time.time()
        threads = [threading.Thread(target=lane_worker, args=(k, b, lanes[k], errors)) for k in range(LANES)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise SystemExit("STOP: " + "; ".join(errors))
        block_secs.append(time.time() - t0); done_cells += len(cells)
        print(f"[item10] block {b} done ({done_cells} cells, {block_secs[-1]:.0f} s)", flush=True)
    tally = merge_tallies(tallies)
    _write_cardinality(tally, run_id, rp.cardinality)
    (rp.root / "item10_status.json").write_text(json.dumps({"rules": RULES, "status": status, "cells": done_cells,
                                                            "blocks": len(block_secs), "block_seconds": block_secs,
                                                            "generated": tally["generated"],
                                                            "excluded_prefix": len(tally["excluded_prefix"])}, indent=1))
    print(f"[item10] {tally['generated']} continuations; {status} -> {rp.root}", flush=True)


if __name__ == "__main__":
    main()
