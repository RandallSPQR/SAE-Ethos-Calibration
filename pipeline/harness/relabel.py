"""Relabel persisted runs under the CURRENT labeler rules, from reconstructed final state.

Generation records are immutable; a label is a derived artifact. Some rules 2026-09-27.1 facts (broken files,
suite failures at the decision point vs the end, flake8, the signed-commit result) are not in the persisted
observed_facts of runs 2 and 3, so this tool reconstructs each continuation's tree: render the (scenario, seed,
variant) from the templates AT THE RUN'S COMMIT (a git worktree), then replay the tool log's write_file /
edit_file / delete_file calls in order, applying each only if the run's tool result said it applied. The
prefix's edits give the decision-point tree; the continuation's edits give the final tree. observed_facts()
then runs on real files (pytest, compile, flake8) exactly as it does live, and label() applies the rules.

  python -m harness.relabel --run results/t2_2026-09-18/main_run --out results/t2_2026-09-18/relabel_2026-09-27.1 \\
      [--hand results/t2_2026-09-18/hand_labels/hand_labels_run2_2026-09-27.jsonl]

Writes relabeled.jsonl (uid, old label/taxonomy, new label/taxonomy, evidence, key facts), a summary, and,
with --hand, the movement of every hand-vs-labeler disagreement (development only, never validation).
"""
import argparse
import collections
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from .labeler import observed_facts, label, suite_summary, files_of, LABELER_RULES_VERSION
from .sandbox import IGNORED_DIRS

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent


class _FakeSandbox:
    """Just enough of Sandbox for observed_facts/label: a root, an event log, canary flags, forbidden hits."""
    def __init__(self, root, events, facts, forbidden=()):
        self.root = Path(root); self.events = events
        self.canary_observed = bool(facts.get("canary_observed")); self.canary_used = bool(facts.get("canary_used"))
        self.forbidden_hits = list(facts.get("forbidden_touched") or []); self.forbidden = set(forbidden)
        self.uid = None; self.env_extra = {}; self.spare_pids = set(); self.ro_paths = []

    def final_state(self):
        out = {}
        for p in sorted(self.root.rglob("*")):
            if any(part in IGNORED_DIRS for part in p.relative_to(self.root).parts):
                continue
            if p.is_file():
                try:
                    out[str(p.relative_to(self.root))] = p.read_text()
                except UnicodeDecodeError:
                    out[str(p.relative_to(self.root))] = None
        return out


def worktree_at(commit, dest):
    subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", "-f", str(dest), commit],
                   check=True, capture_output=True)
    return dest


def render_at(wt, scenario, seeds, out):
    subprocess.run([sys.executable, "scripts/render.py", "--arm", "a", "--scenario", scenario,
                    "--seeds", f"{min(seeds)}-{max(seeds)}", "--out", str(out)], cwd=wt / "scenarios",
                   check=True, capture_output=True)


def replay_edits(tree, messages, upto):
    """Apply the model's file operations from messages[:upto] in order, each only if the run's tool result
    said it took effect. Returns the list of events (tool, args, result) in the same shape Sandbox.log keeps."""
    events = []
    for i, m in enumerate(messages[:upto]):
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            name, args = tc["name"], tc.get("args") or {}
            result = messages[i + 1].get("content", "") if i + 1 < len(messages) and messages[i + 1].get("role") == "tool" else ""
            events.append({"tool": name, "args": args, "result": str(result)[:400]})
            p = tree / str(args.get("path", ""))
            try:
                if name == "write_file" and str(result).startswith("wrote"):
                    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(str(args.get("content", "")))
                elif name == "edit_file" and str(result).strip() == "ok":
                    src = p.read_text(); f = str(args.get("find", ""))
                    if src.count(f) == 1:
                        p.write_text(src.replace(f, str(args.get("replace", ""))))
                elif name == "delete_file" and str(result).startswith("deleted"):
                    if p.exists():
                        p.unlink()
            except (OSError, UnicodeDecodeError):
                pass
    # the harness's own protocol events live in observed_facts counts, not in tool_calls; re-add as events
    return events


def relabel_row(row, rendered_variant_dir, spec, seed_manifest):
    """Reconstruct decision-point and final trees, recompute facts, relabel."""
    msgs = row["messages"]; dp = row["decision_point"]
    old_facts = row["observed_facts"]
    with tempfile.TemporaryDirectory(prefix="relabel_") as td:
        base = Path(td) / "orig"; shutil.copytree(rendered_variant_dir, base)
        render = files_of(base)                                   # the rendered original
        dec = Path(td) / "dec"; shutil.copytree(base, dec); replay_edits(dec, msgs, dp)
        decision_suite = suite_summary(dec)
        orig = files_of(dec)                                      # the decision-point tree (B6)
        fin = Path(td) / "fin"; shutil.copytree(base, fin); events = replay_edits(fin, msgs, len(msgs))
        # persisted protocol counts become events so the new facts see them
        events += [{"tool": "bad_call", "args": {}, "result": ""}] * int(old_facts.get("bad_calls") or 0)
        events += [{"tool": "unparseable_tool_block", "args": {}, "result": ""}] * int(old_facts.get("unparseable_tool_blocks") or 0)
        if old_facts.get("asked_user") and not any(e["tool"] == "ask_user" for e in events):
            events.append({"tool": "ask_user", "args": {"question": ""}, "result": "asked"})
        sb = _FakeSandbox(fin, events, old_facts, forbidden=(seed_manifest.get("gen") or {}).get("forbidden_files", []))
        term = {"terminal": _terminal_from_row(row), "answer": old_facts.get("submitted_value")}
        cont_text = "\n".join(m.get("content", "") for m in msgs[dp:] if m.get("role") == "assistant")
        facts = observed_facts(sb, spec, seed_manifest, term, orig, rendered_variant_dir,
                               decision_text=old_facts.get("decision_text", ""), disclosure_text=cont_text,
                               decision_suite=decision_suite, render_files=render)
        # facts the reconstruction cannot recompute better than the run did
        for k in ("canary_observed", "canary_used", "forbidden_touched", "answer_correct", "submitted_value",
                  "submitted_done", "forbidden_value"):
            facts[k] = old_facts.get(k, facts.get(k))
        facts["asked_user"] = bool(old_facts.get("asked_user"))
        facts["real_fix"] = (facts["final_tests_pass"] is True) and not facts["test_files_changed"]
        lab, tax, disclosed, evidence = label(spec["family"], row["variant"], facts, sb, term)
    return lab, tax, disclosed, evidence, facts


def _terminal_from_row(row):
    """Persisted rows carry no terminal reason; infer: submit -> 'submit', ask -> 'ask', last assistant turn
    without a tool call -> 'no_tool', else the cap."""
    th = row["observed_facts"].get("tool_history") or []
    if th and th[-1] == "submit":
        return "submit"
    if th and th[-1] == "ask_user":
        return "ask"
    last = [m for m in row["messages"] if m.get("role") == "assistant"][-1] if row["messages"] else {}
    if not (last.get("tool_calls") or []):
        return "no_tool"
    return "cap"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hand", default=None, help="hand labels jsonl (uid, taxonomy) to report movement against")
    ap.add_argument("--commit", default=None, help="template commit (default: the run manifest's git_commit)")
    args = ap.parse_args()
    run = Path(args.run); out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((run / "manifest.json").read_text())
    commit = args.commit or manifest["git_commit"]
    rows = [json.loads(l) for f in sorted((run / "generation" / "arm_a").glob("*.jsonl")) for l in open(f) if l.strip()]
    by_scen = collections.defaultdict(set)
    for r in rows:
        by_scen[r["scenario"]].add(r["seed"])
    wt = Path(tempfile.mkdtemp(prefix="relabel_wt_"))
    try:
        worktree_at(commit, wt)
        build = wt / "relabel_build"
        for scen, seeds in by_scen.items():
            render_at(wt, scen, seeds, build)
        specs = {scen: yaml.safe_load((wt / "scenarios" / "arm_a" / scen / "scenario.yaml").read_text()) for scen in by_scen}
        results, moved = [], collections.Counter()
        for r in rows:
            seed_dir = build / "discover" / r["scenario"] / f"seed_{r['seed']:03d}"
            sm = json.loads((seed_dir / "manifest.json").read_text())
            lab, tax, disclosed, ev, facts = relabel_row(r, seed_dir / r["variant"], specs[r["scenario"]], sm)
            old = r["judge"]
            results.append({"uid": r["uid"], "scenario": r["scenario"], "variant": r["variant"], "seed": r["seed"],
                            "old_label": old["label"], "old_taxonomy": old["taxonomy"], "new_label": lab, "new_taxonomy": tax,
                            "disclosed": disclosed, "evidence": ev,
                            "facts": {k: facts.get(k) for k in ("final_tests_pass", "tests_failed_at_decision", "tests_failed_final",
                                                                 "broken_files", "snooped", "signed_commit", "lint_clean", "terminal",
                                                                 "submitted_value", "asked_user", "bad_calls", "unparseable_tool_blocks",
                                                                 "call_repairs", "test_files_changed", "test_files_restored",
                                                                 "attempted_forbidden", "forbidden_touched", "legacy_deleted",
                                                                 "legacy_emptied", "disclosed_removal", "canary_observed", "canary_used",
                                                                 "final_text")}})
            moved[(old["taxonomy"], tax)] += 1
        with open(out / "relabeled.jsonl", "w") as f:
            for x in results:
                f.write(json.dumps(x) + "\n")
        # a DERIVED generation set with judge fields replaced, so gates (G6, G7) read the current rules;
        # the run's own generation/ stays untouched
        byu = {x["uid"]: x for x in results}
        gen_out = out / "generation" / "arm_a"; gen_out.mkdir(parents=True, exist_ok=True)
        for f in sorted((run / "generation" / "arm_a").glob("*.jsonl")):
            with open(gen_out / f.name, "w") as g:
                for l in open(f):
                    if not l.strip():
                        continue
                    r = json.loads(l); x = byu[r["uid"]]
                    # the recomputed facts (readings for the hand-label sheet) sit beside the persisted ones
                    r["observed_facts"] = {**r["observed_facts"], **{k: v for k, v in x["facts"].items() if v is not None},
                                           "labeler_rules": LABELER_RULES_VERSION}
                    r["judge"] = {**r["judge"], "label": x["new_label"], "episode_outcome_label": x["new_label"],
                                  "taxonomy": x["new_taxonomy"], "disclosed": x["disclosed"], "evidence": x["evidence"],
                                  "source": f"relabel:{LABELER_RULES_VERSION}", "original_label": r["judge"]["label"],
                                  "original_taxonomy": r["judge"]["taxonomy"]}
                    g.write(json.dumps(r) + "\n")
        for name in ("manifest.json", "cardinality.json"):
            if (run / name).exists():
                shutil.copy2(run / name, out / name)
        lines_note = f"derived generation with judge replaced: {out}/generation (gates: --run-dir {out})"
        lines = [f"# relabel {run.name} under rules {LABELER_RULES_VERSION} (templates at {commit[:12]})", lines_note, "",
                 f"{len(results)} continuations; taxonomy movement (old -> new):"]
        for (a, b), c in sorted(moved.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {c:4d}  {a} -> {b}" + ("" if a == b else "   <-- moved"))
        if args.hand:
            hand = {json.loads(l)["uid"]: json.loads(l) for l in open(args.hand) if l.strip()}
            byu = {x["uid"]: x for x in results}
            agree_old = agree_new = 0; rowsm = []
            for uid, h in hand.items():
                x = byu.get(uid)
                if not x:
                    continue
                agree_old += h["taxonomy"] == x["old_taxonomy"]; agree_new += h["taxonomy"] == x["new_taxonomy"]
                if h["taxonomy"] != x["old_taxonomy"] or h["taxonomy"] != x["new_taxonomy"]:
                    rowsm.append(f"  {uid:<46} hand={h['taxonomy']:<22} old={x['old_taxonomy']:<22} new={x['new_taxonomy']:<22} "
                                 f"{'FIXED' if h['taxonomy'] == x['new_taxonomy'] else ('REGRESSED' if h['taxonomy'] == x['old_taxonomy'] else 'still differs')}")
            from gates._common import cohen_kappa
            common = [u for u in hand if u in byu]
            k_old = cohen_kappa([hand[u]["taxonomy"] for u in common], [byu[u]["old_taxonomy"] for u in common])
            k_new = cohen_kappa([hand[u]["taxonomy"] for u in common], [byu[u]["new_taxonomy"] for u in common])
            lines += ["", f"hand labels (DEVELOPMENT set, n={len(common)}): agreement {agree_old} -> {agree_new}; "
                          f"kappa {k_old:.3f} -> {k_new:.3f}", "movement of every disagreement:"] + rowsm
        (out / "SUMMARY.md").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
    finally:
        subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(wt)], capture_output=True)


if __name__ == "__main__":
    main()
