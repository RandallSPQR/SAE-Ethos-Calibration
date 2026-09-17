"""Orchestrate the real Arm-A harness with TRUE decision-point resampling:

  1. build_prefix: run the deterministic (T=0) trajectory to the obstacle reveal, stopping before the
     decision turn. Snapshot filesystem + audit-event state.
  2. for each continuation k: restore that snapshot AND the prefix messages, then continue_from() —
     the model sees the SAME conversation and the SAME world, and its first turn IS the decision.

This is the measuring instrument for Arm A (labels from real final state). Petri is elsewhere.

Continuation randomness is derived from immutable identity (run_id, scenario, seed, variant, k), not a
bare loop index, so cells don't share RNG structure and every continuation is reproducible.
"""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path

import yaml

from resample.target_client import TargetClient
from .sandbox import Sandbox
from .agent_loop import build_prefix, continue_from
from .labeler import label, observed_facts, decision_action_label, temporal_spans
from .isolation import assert_isolated
from . import confine
from .triggers import build_matcher, DecisionPointNotReached
from .contract import check_scenario, effective_tools

ROOT = Path(__file__).resolve().parent.parent
SCEN = ROOT.parent / "scenarios"


def derive_seed(run_id, scenario, seed, variant, k):
    h = hashlib.sha256(f"{run_id}|{scenario}|{seed}|{variant}|{k}".encode()).hexdigest()
    return int(h[:8], 16)


def run_scenario_seed(client, scenario, seed_dir, manifest, samp, n, out_dir, ledger, run_id, tally):
    family = scenario["family"]
    surface = manifest["surface"]
    task = _subst(scenario["task_prompt"], surface)
    cap = scenario["gates"]["max_tool_calls"]
    variants_spec = scenario.get("variants", {})
    canary = manifest.get("canary")
    forbidden = (manifest.get("gen") or {}).get("forbidden_files", [])
    rows = []

    for variant in ("full", "control", "mention"):
        vdir = seed_dir / variant
        if not vdir.exists():
            continue
        vspec = variants_spec.get(variant, {})
        tools = effective_tools(scenario, vspec)
        scen_v = dict(scenario, tools=tools)          # variant-specific tool set (delete_file/bash where granted)
        allow_delete = "delete_file" in tools
        orig_files = _rendered_files(vdir)

        # 1) deterministic prefix to the STRUCTURED trigger + snapshot (filesystem + event state)
        sb0 = Sandbox(vdir, canary=canary, forbidden_files=forbidden, allow_delete=allow_delete)
        try:
            matcher = build_matcher(scenario, surface)
            prefix_msgs, start_step = build_prefix(
                client, sb0, scen_v, task, matcher, cap, samp["max_new_tokens"])
        except DecisionPointNotReached as e:
            tally["excluded_prefix"].append({"scenario": scenario["id"], "seed": manifest["seed"],
                                             "variant": variant, "reason": str(e)[:200]})
            _reach(tally, scenario["id"], variant, reached=False)
            print(f"EXCLUDED {scenario['id']}/seed_{manifest['seed']:03d}/{variant}: {str(e)[:200]}", flush=True)
            sb0.cleanup()
            continue
        except Exception as e:                   # a harness/model-input failure in THIS cell: recorded, run continues
            tally["excluded_prefix"].append({"scenario": scenario["id"], "seed": manifest["seed"],
                                             "variant": variant, "reason": f"harness_exception: {type(e).__name__}: {str(e)[:160]}"})
            _reach(tally, scenario["id"], variant, reached=False)
            print(f"HARNESS EXCEPTION (cell excluded) {scenario['id']}/seed_{manifest['seed']:03d}/{variant}: "
                  f"{type(e).__name__}: {str(e)[:300]}", flush=True)
            import traceback; traceback.print_exc()
            sb0.cleanup()
            continue
        _reach(tally, scenario["id"], variant, reached=True)
        snap = sb0.snapshot()

        # 2) N continuations, each on a RESTORED world + RESTORED conversation
        for k in range(n):
            sb = Sandbox(vdir, canary=canary, forbidden_files=forbidden, allow_delete=allow_delete)
            sb.restore(snap)
            kseed = derive_seed(run_id, scenario["id"], manifest["seed"], variant, k)
            msgs, dp, term, dtok = continue_from(
                client, sb, scen_v, prefix_msgs, start_step, cap,
                samp["temperature"], samp["top_p"], samp["max_new_tokens"], seed=kseed,
                escalation_turns=scenario.get("escalation_turns"))
            cont_text = "\n".join(m.get("content", "") for m in msgs[dp:] if m.get("role") == "assistant")
            facts = observed_facts(sb, scenario, manifest, term, orig_files, vdir,
                                   decision_text=(dtok or {}).get("text", ""), disclosure_text=cont_text)
            lab, tax, disclosed, evidence = label(family, variant, facts, sb, term)
            uid = f"{scenario['id']}/seed_{manifest['seed']:03d}/{variant}/c{k:02d}"
            dp = dp if dp is not None else len(msgs) - 1
            spans = temporal_spans(msgs, dp, sb, facts)
            dec_action = decision_action_label(msgs[dp] if dp < len(msgs) else {})
            rows.append({
                "uid": uid, "scenario": scenario["id"], "arm": "A", "seed": manifest["seed"],
                "split": "discover" if manifest["seed"] <= 49 else "test", "variant": variant,
                "persona_prompted": "neutral", "steer": {"vector": None, "strength": 0.0},
                "rng_seed": kseed, "run_id": run_id,
                "messages": msgs, "decision_point": dp,
                # scored_message_index defaults to the decision turn; replay can score another span
                "scored_message_index": dp, "spans": spans,
                # the temperature the decision turn was generated at: G1 picks its criterion from THIS field
                "sampling": {"temperature": samp["temperature"], "top_p": samp["top_p"]},
                "tokens": {"sampled_ids": (dtok or {}).get("sampled_ids"),
                           "sampled_logprobs": (dtok or {}).get("sampled_logprobs"),
                           "sampled_top2_margin": (dtok or {}).get("sampled_top2_margin")},
                "observed_facts": facts,          # WORLD/INTERPRETATION facts, persisted for RELABELING
                "judge": {
                    # TWO distinct labels: what the model did AT the decision, and how the episode ended.
                    "decision_action_label": dec_action,
                    "episode_outcome_label": lab, "taxonomy": tax,
                    "label": lab,                 # back-compat alias == episode_outcome_label
                    "disclosed": disclosed, "confidence": 1.0, "evidence": evidence,
                    "source": "real_state"},
                "cost_usd": 0.0,
            })
            tally["generated"] += 1
            sb.cleanup()
            with open(ledger, "a") as f:
                f.write(json.dumps({"uid": uid, "cost_usd": 0.0}) + "\n")
        sb0.cleanup()
        _rmsnap(snap)

    outp = Path(out_dir) / "arm_a"
    outp.mkdir(parents=True, exist_ok=True)
    with open(outp / f"{scenario['id']}__seed{manifest['seed']:03d}.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"{scenario['id']}/seed_{manifest['seed']:03d}: {len(rows)} continuations written "
          f"(labels: {collections.Counter(r['judge']['episode_outcome_label'] for r in rows) if rows else '{}'})", flush=True)
    _write_cardinality(tally, run_id, Path(out_dir).parent / "cardinality.json")   # incremental: survives a later crash
    return len(rows)


def _write_cardinality(tally, run_id, path):
    reach = {k: {**v, "p_reach": round(v["reached"] / v["attempted"], 3) if v["attempted"] else None}
             for k, v in tally["reach"].items()}
    card = {"run_id": run_id, "generated": tally["generated"],
            "excluded_prefix_count": len(tally["excluded_prefix"]),
            "excluded_prefix": tally["excluded_prefix"],
            "reach": reach,
            "estimand_note": "measured quantity is P(transgress | greedy prefix reached obstacle); "
                             "report p_reach alongside conditional transgression rates."}
    Path(path).write_text(json.dumps(card, indent=2))
    return card


def _reach(tally, scenario_id, variant, reached):
    """Per-cell reach accounting for the estimand: P(reach obstacle) and P(transgress | reached)."""
    key = f"{scenario_id}/{variant}"
    r = tally["reach"].setdefault(key, {"attempted": 0, "reached": 0})
    r["attempted"] += 1
    if reached:
        r["reached"] += 1


def _rmsnap(snap):
    import shutil
    shutil.rmtree(snap["dir"], ignore_errors=True)


def _rendered_files(d):
    out = {}
    for p in sorted(Path(d).rglob("*")):
        if p.is_file():
            try:
                out[str(p.relative_to(d))] = p.read_text()
            except UnicodeDecodeError:
                out[str(p.relative_to(d))] = None
    return out


def _subst(text, surface):
    for k, v in surface.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=str(SCEN / "build"))
    ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--allow-unsafe", action="store_true", help="bypass isolation check (mock/dev only)")
    ap.add_argument("--require-pinned", action="store_true",
                    help="T3: refuse to run unless provenance is fully pinned (no floating revisions/hashes)")
    args = ap.parse_args()
    run = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())
    samp = run["sampling"]
    ledger = run["cost"]["ledger_path"]
    # ENFORCED isolation precondition for real runs; mock/dev must pass --allow-unsafe explicitly.
    # Real runs: pick a confinement backend by running its five canaries NOW (harness.confine), and hide
    # everything the episode must never see (run tree, rendered build incl. _side/ answers, HF_HOME, ledger).
    unsafe = args.mock or args.allow_unsafe
    confinement = None if unsafe else confine.select(prefer=os.environ.get("ARM_A_ISOLATION"),
                                                     uds=os.environ.get("TARGET_UDS"))
    iso = assert_isolated(run, allow_unsafe=unsafe, confinement=confinement)
    if confinement is not None:
        iso["hardened"] = confine.harden([args.runs_root, args.build, os.environ.get("HF_HOME"), ledger])
        print(f"confinement: backend={confinement['backend']} canaries={confinement['canaries']} "
              f"(tried {[(b, t['canaries']) for b, t in confinement['tried'].items()]})")
    client = TargetClient(mock=args.mock)
    # pin instrument identity FIRST; run_id commits to it (run_id = H(manifest))
    from provenance import resolve_and_write, assert_pinned
    from runpaths import RunPaths
    run_id, manifest = _resolve_run(args, run, resolve_and_write)
    rp = RunPaths(args.runs_root, run_id).ensure()
    manifest["isolation"] = iso                    # backend + canary results, not a YAML field set to true
    (rp.manifest).write_text(json.dumps(manifest, indent=2))
    if args.require_pinned:
        assert_pinned(manifest)          # hard refusal on any floating/null identity field
    elif manifest.get("unverified"):
        print(f"manifest UNVERIFIED fields (fill before a pinned run; --require-pinned enforces): "
              f"{manifest['unverified']}")

    tally = {"generated": 0, "excluded_prefix": [], "reach": {}}
    gen_dir = rp.generation
    for spec_path in sorted((SCEN / "arm_a").glob("*/scenario.yaml")):
        scenario = yaml.safe_load(spec_path.read_text())
        check_scenario(scenario)          # declared==documented==executor per variant; fatal on mismatch
        for split in ("discover", "test"):
            base = Path(args.build) / split / scenario["id"]
            for seed_dir in sorted(base.glob("seed_*")) if base.exists() else []:
                sm = json.loads((seed_dir / "manifest.json").read_text())
                run_scenario_seed(client, scenario, seed_dir, sm, samp, args.n,
                                  str(gen_dir), ledger, run_id, tally)
    # cardinality + estimand: P(reach obstacle) and (later) P(transgress | reached), per cell
    _write_cardinality(tally, run_id, rp.cardinality)
    print(f"harness: {tally['generated']} continuations (run_id={run_id}); "
          f"{len(tally['excluded_prefix'])} cells excluded -> {rp.root}")


def _resolve_run(args, run, resolve_and_write):
    """Build the manifest and derive run_id = H(manifest). --run-id only overrides for mock/dev."""
    run_id, manifest = resolve_and_write(scenarios_dir=str(SCEN), override_run_id=args.run_id)
    return run_id, manifest


def _new_run_id():
    import time
    return "run_" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:10]


if __name__ == "__main__":
    main()
