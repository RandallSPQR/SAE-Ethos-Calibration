#!/usr/bin/env python3
"""Item 9: four arms at turn_close (analyze/PREREG_ITEM9_TURN_CLOSE.md; rules ITEM9_RULES).

ONE sentence at ONE place, the closing line of the first user turn (after the task text; harness.agent_loop.build_prefix
insert "turn_close"), on impossible_test/full with the scenario unchanged (its escalation turn on). Four arms, the lists
reused verbatim: neutral[i], pc[i], grader[i] (item 7), pressure[i] (item 8).

Conditions c = 0..79: seed c mod 40, paraphrase (c + c // 40) mod 8, so the 80 (seed, paraphrase) pairs are distinct,
each seed carries 2 paraphrases and each paraphrase 10 seeds. Every condition runs all four arms on the same seed and
paraphrase index, in an order rotated by c mod 4; 4 continuations per fresh greedy prefix at the T3 settings, exactly as
harness.run_harness does. 80 x 4 = 320 per arm, 1,280 in all.

Throughput: the harness runs a cell's continuations concurrently (run.yaml harness.concurrency 8), so a 4-continuation
cell costs nearly what an 8-continuation cell does. Two cells therefore run side by side ("lanes"), keeping 8 sequences
on vLLM as in items 7 and 8. Each lane writes its own directory, keeps its own tally (merged at the end) and uses its own
range of sandbox slots, hence episode uids (lane_slot_base): sandbox cleanup reaps and sweeps per uid, so lanes sharing
uids kill each other's processes and delete each other's /tmp trees (attempt 1 STOPped on exactly that).

Deadline guard: the run proceeds in blocks of 8 conditions (each block holds every paraphrase once and every arm in every
condition: 32 cells). A block starts only if it is projected to end before deadline - margin; the projection is the mean
wall time of this run's earlier blocks, or PRIOR_BLOCK_S before the first.

  python -m harness.run_item9 --build ../scenarios/build_item9 --runs-root <dir> [--deadline 2026-10-08T20:00:00Z]
      [--margin-min 30] [--require-pinned] [--mock --allow-unsafe] [--max-blocks K]   (K: mock / smoke only)
  python -m harness.run_item9 --check-lengths      (tokenizer-dependent pair-length check, +-2 tokens)
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

ROOT = Path(__file__).resolve().parent.parent
SCEN = ROOT.parent / "scenarios"
RULES = "2026-10-08.2"
PARAPHRASES = ROOT / "config" / "item9_paraphrases.yaml"
FREEZE = ROOT / "harness" / "ITEM9_FREEZE.json"
POSITION = "turn_close"
ARMS = ("neutral", "pc", "grader", "pressure")
SCENARIO, N = "impossible_test", 4
N_CONDITIONS, N_SEEDS, N_PARA, BLOCK, LANES = 80, 40, 8, 8, 2
SLOT_STRIDE = 16               # lane k uses sandbox slots 16k .. 16k + N (prefix + N continuations); N + 1 <= 16


def lane_slot_base(lane):
    return lane * SLOT_STRIDE
# item 8: ~151 s per 8-continuation cell (all arms); a 4-continuation cell at ~0.8 of that, two lanes in parallel
PRIOR_BLOCK_S = BLOCK * len(ARMS) * 0.8 * 151.0 / LANES


def paraphrases():
    data = PARAPHRASES.read_bytes()
    p = yaml.safe_load(data)
    assert all(len(p[a]) == N_PARA for a in ARMS)
    return p, hashlib.sha256(data).hexdigest()


def conditions():
    """[(c, seed, paraphrase index, arm order)]."""
    out = []
    for c in range(N_CONDITIONS):
        k = c % len(ARMS)
        out.append((c, c % N_SEEDS, (c + c // N_SEEDS) % N_PARA, ARMS[k:] + ARMS[:k]))
    return out


def seed_dir(build, scenario, seed):
    hits = sorted(Path(build).glob(f"*/{scenario}/seed_{seed:03d}"))
    if not hits:
        raise FileNotFoundError(f"no rendered seed {seed} for {scenario} under {build}")
    return hits[0]


def check_lengths(tok, p, max_diff=2):
    rows = []
    for i, n in enumerate(p["neutral"]):
        ln = len(tok.encode(n, add_special_tokens=False))
        row = {"i": i, "neutral_tokens": ln}
        for arm in ARMS[1:]:
            row[f"{arm}_tokens"] = len(tok.encode(p[arm][i], add_special_tokens=False))
        row["ok"] = all(abs(row[f"{a}_tokens"] - ln) <= max_diff for a in ARMS[1:])
        rows.append(row)
    return rows


def merge_tallies(tallies):
    out = {"generated": 0, "excluded_prefix": [], "reach": {}, "operator_nudge": tallies[0].get("operator_nudge")}
    for t in tallies:
        out["generated"] += t["generated"]; out["excluded_prefix"] += t["excluded_prefix"]
        for k in ("excluded_continuation",):
            if t.get(k):
                out.setdefault(k, []).extend(t[k])
        out["overran_turn"] = out.get("overran_turn", 0) + int(t.get("overran_turn", 0))
        for key, r in t["reach"].items():
            o = out["reach"].setdefault(key, {"attempted": 0, "reached": 0, "reached_no_nudge": 0})
            for f in o:
                o[f] += r.get(f, 0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=str(SCEN / "build_item9")); ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--mock", action="store_true"); ap.add_argument("--allow-unsafe", action="store_true")
    ap.add_argument("--require-pinned", action="store_true"); ap.add_argument("--run-id", default=None)
    ap.add_argument("--deadline", default=None, help="UTC ISO time; no new block starts if it cannot finish before deadline - margin")
    ap.add_argument("--margin-min", type=float, default=30.0)
    ap.add_argument("--max-blocks", type=int, default=None, help="mock / smoke only")
    ap.add_argument("--check-lengths", action="store_true")
    a = ap.parse_args()
    p, psha = paraphrases()
    if FREEZE.exists() and json.loads(FREEZE.read_text())["paraphrases_sha256"] != psha:
        raise SystemExit("STOP: config/item9_paraphrases.yaml differs from the frozen sha in harness/ITEM9_FREEZE.json")
    if a.check_lengths:
        import modelcfg
        from transformers import AutoTokenizer
        tm = modelcfg.target()
        tok = AutoTokenizer.from_pretrained(tm["hf_id"], revision=tm.get("revision"))
        rows = check_lengths(tok, p)
        print(json.dumps(rows, indent=1))
        raise SystemExit(0 if all(r["ok"] for r in rows) else 1)
    if not a.mock and not FREEZE.exists():
        raise SystemExit("STOP: harness/ITEM9_FREEZE.json missing: paraphrases are not frozen")
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
    manifest["item9"] = {"rules": RULES, "paraphrases_sha256": psha, "arms": ARMS, "position": POSITION,
                         "scenario": SCENARIO, "n": N, "lanes": LANES, "block": BLOCK}
    rp.manifest.write_text(json.dumps(manifest, indent=2))
    if a.require_pinned:
        assert_pinned(manifest)
    hcfg = run.get("harness") or {}
    nudge = {"text": (hcfg.get("operator_nudge") or "").strip() or None, "max": int(hcfg.get("nudge_max", 1))}
    tallies = [{"generated": 0, "excluded_prefix": [], "reach": {}, "operator_nudge": nudge} for _ in range(LANES)]
    deadline = dt.datetime.fromisoformat(a.deadline.replace("Z", "+00:00")).timestamp() if a.deadline else None
    log = rp.root / "item9_progress.jsonl"
    log_lock = threading.Lock()
    spec = yaml.safe_load((SCEN / "arm_a" / SCENARIO / "scenario.yaml").read_text())
    check_scenario(spec)

    def run_cell(lane, c, seed, i, arm, order):
        sd = seed_dir(a.build, SCENARIO, seed)
        sm = json.loads((sd / "manifest.json").read_text())
        tag = f"{arm}_p{i}_s{seed:03d}_c{c:02d}"
        extra = {"item9": {"rules": RULES, "arm": arm, "paraphrase": i, "position": POSITION, "condition": c,
                           "sentence": p[arm][i], "order": list(order), "lane": lane}}
        t0 = time.time()
        run_scenario_seed(client, spec, sd, sm, samp, N, str(rp.generation / SCENARIO / f"lane{lane}" / tag), ledger,
                          run_id, tallies[lane], hcfg=hcfg, variants={"full"},
                          insert={"text": p[arm][i], "position": POSITION}, row_extra=extra, slot_base=lane_slot_base(lane))
        with log_lock, open(log, "a") as fh:
            fh.write(json.dumps({"tag": tag, "lane": lane, "seconds": time.time() - t0, "t": time.time()}) + "\n")

    def lane_worker(lane, cells, errors):
        try:
            for cell in cells:
                run_cell(lane, *cell)
        except BaseException as e:                                  # surfaced after the join: a lane error STOPs the run
            errors.append(f"lane {lane}: {type(e).__name__}: {e}")

    conds = conditions()
    blocks = [conds[k:k + BLOCK] for k in range(0, len(conds), BLOCK)]
    if a.max_blocks:
        blocks = blocks[: a.max_blocks]
    status, block_secs, done_cells = "complete", [], 0
    for b, block in enumerate(blocks):
        per = (sum(block_secs) / len(block_secs)) if block_secs else PRIOR_BLOCK_S
        if deadline is not None and time.time() + per > deadline - 60 * a.margin_min:
            status = f"cut before block {b} (condition {block[0][0]}) by the deadline guard"
            print(f"[item9] CUT before block {b} (time guard; est {per:.0f} s/block)", flush=True)
            break
        cells = [(c, seed, i, arm, order) for c, seed, i, order in block for arm in order]
        lanes = [cells[k::LANES] for k in range(LANES)]
        errors, t0 = [], time.time()
        threads = [threading.Thread(target=lane_worker, args=(k, lanes[k], errors)) for k in range(LANES)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise SystemExit("STOP: " + "; ".join(errors))
        block_secs.append(time.time() - t0); done_cells += len(cells)
        print(f"[item9] block {b} done ({done_cells} cells, {block_secs[-1]:.0f} s)", flush=True)
    tally = merge_tallies(tallies)
    _write_cardinality(tally, run_id, rp.cardinality)
    (rp.root / "item9_status.json").write_text(json.dumps({"rules": RULES, "status": status, "cells": done_cells,
                                                           "blocks": len(block_secs), "block_seconds": block_secs,
                                                           "generated": tally["generated"],
                                                           "excluded_prefix": len(tally["excluded_prefix"])}, indent=1))
    print(f"[item9] {tally['generated']} continuations; {status} -> {rp.root}", flush=True)


if __name__ == "__main__":
    main()
