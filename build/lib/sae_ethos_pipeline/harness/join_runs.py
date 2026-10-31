"""Join generation records from several run directories into one analysis directory (T3: two pod sessions with
different commits, RUNBOOK T3 sessions 2026-09-28). Every uid must appear exactly once in the union; a file that is
superseded (session 1's missing_delete_tool files, lost 7 continuations to a harness bug) is dropped EXPLICITLY with
--drop, never last-write-wins. Writes generation/arm_a/<run_id>__<file>.jsonl (records carry their own run_id),
manifest.json (the member manifests, verbatim, under "members"; identity fields that must agree are checked), and
cardinality.json rebuilt from the generation files (attempted = seed files present per scenario; reached per variant
= seeds with >= 1 continuation of that variant), because the harness rewrites cardinality.json per invocation.

  python -m harness.join_runs --out results/t3_2026-09-28_joined \
      --run results/t3_2026-09-27_session1/main_run --drop 'missing_delete_tool__*' \
      --run results/t3_2026-09-28_session2/main_run
"""
import argparse
import collections
import fnmatch
import json
import shutil
from pathlib import Path

IDENTITY = [("model", "revision"), ("model", "weight_hash"), ("tokenizer", "chat_template_hash"), ("sae", "weights_hash"),
            ("oracle", "weights_hash"), ("software", "vllm"), ("config", "run_yaml_hash"), ("config", "models_yaml_hash")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--run", action="append", required=True, help="run directory; repeatable, in order")
    ap.add_argument("--drop", action="append", default=[], help="glob of generation files to drop from the PRECEDING --run")
    args = ap.parse_args()
    # pair each --drop with the --run it follows (argparse gives flat lists; re-read argv order)
    import sys
    runs, drops, cur = [], {}, None
    it = iter(sys.argv[1:])
    for a in it:
        if a == "--run":
            cur = next(it); runs.append(cur); drops[cur] = []
        elif a == "--drop":
            drops[cur].append(next(it))
    out = Path(args.out); gen = out / "generation" / "arm_a"
    if out.exists():
        shutil.rmtree(out)
    gen.mkdir(parents=True)
    members, seen, kept, dropped = [], {}, 0, []
    for r in runs:
        r = Path(r); m = json.loads((r / "manifest.json").read_text()); members.append(m)
        for f in sorted((r / "generation" / "arm_a").glob("*.jsonl")):
            if any(fnmatch.fnmatch(f.name, g) for g in drops.get(str(r), [])):
                dropped.append(f"{m['run_id']}/{f.name}"); continue
            rows = [json.loads(l) for l in open(f) if l.strip()]
            for x in rows:
                if x["uid"] in seen:
                    raise SystemExit(f"duplicate uid {x['uid']} in {m['run_id']} and {seen[x['uid']]}; drop one explicitly")
                seen[x["uid"]] = m["run_id"]
            shutil.copy2(f, gen / f"{m['run_id']}__{f.name}"); kept += len(rows)
    for path in IDENTITY:
        vals = {json.dumps(_get(m, path)) for m in members}
        if len(vals) != 1:
            raise SystemExit(f"members disagree on {'.'.join(path)}: {vals}")
    manifest = {"joined": True, "run_ids": [m["run_id"] for m in members], "members": members,
                "dropped_files": dropped, "identity_checked": [".".join(p) for p in IDENTITY],
                "gate_rules_version": members[-1].get("gate_rules_version"),
                "note": "labels differ by member commit only where the labeler changed; see harness/LABELER_CHANGELOG.md"}
    for k in ("model", "tokenizer", "sae", "oracle", "software", "config", "isolation", "gate_rules_version"):
        manifest[k] = members[-1].get(k)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    # cardinality from the joined generation files
    attempted = collections.defaultdict(set); reached = collections.defaultdict(set); n = 0
    for f in sorted(gen.glob("*.jsonl")):
        scen, seed = f.name.split("__", 1)[1].rsplit("__seed", 1); seed = int(seed[:3])
        attempted[scen].add(seed)
        for l in open(f):
            if l.strip():
                x = json.loads(l); reached[(scen, x["variant"])].add(seed); n += 1
    reach = {}
    for scen in sorted(attempted):
        for v in ("full", "control", "mention"):
            reach[f"{scen}/{v}"] = {"attempted": len(attempted[scen]), "reached": len(reached[(scen, v)]),
                                    "p_reach": round(len(reached[(scen, v)]) / max(1, len(attempted[scen])), 3),
                                    "seeds_not_reached": sorted(attempted[scen] - reached[(scen, v)])}
    card = {"run_id": "+".join(m["run_id"] for m in members), "generated": n, "reach": reach,
            "excluded_prefix_count": sum(len(x["seeds_not_reached"]) for x in reach.values()),
            "excluded_continuation_count": None,
            "note": "rebuilt from generation files; per-uid harness exclusions are in each member's cardinality.json / cardinality_from_log.json"}
    (out / "cardinality.json").write_text(json.dumps(card, indent=2))
    print(f"joined {len(members)} runs -> {out}: {n} continuations, {len(list(gen.glob('*.jsonl')))} files, dropped {dropped}")
    for k, v in reach.items():
        if v["seeds_not_reached"]:
            print(f"  reach {k}: {v['reached']}/{v['attempted']} (not reached: {v['seeds_not_reached']})")


def _get(m, path):
    for k in path:
        m = (m or {}).get(k)
    return m


if __name__ == "__main__":
    main()
