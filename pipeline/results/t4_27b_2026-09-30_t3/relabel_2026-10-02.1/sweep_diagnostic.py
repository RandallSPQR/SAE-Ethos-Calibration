"""Sweep for labeler rules 2026-10-02.1 (diagnostic-only test edits) on the 27B T3 run. The rule can move a label only
where the continuation left a test file changed and parseable (`tf_intact`); every such row is re-run through
harness.relabel.relabel_row (templates at 3ac7cc8; both suites run), and every other row is unchanged by construction.

  PYTHONPATH=. python results/t4_27b_2026-09-30_t3/relabel_2026-10-02.1/sweep_diagnostic.py
"""
import collections, json, subprocess, shutil, tempfile, yaml
from pathlib import Path
from harness.relabel import worktree_at, render_at, relabel_row, REPO
from harness.labeler import LABELER_RULES_VERSION

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "relabel_2026-10-01.2"
RUN = HERE.parent / "27b/t3/runs/run_df0365347b16"
base = {json.loads(l)["uid"]: json.loads(l) for f in sorted((BASE / "generation/arm_a").glob("*.jsonl")) for l in open(f) if l.strip()}
orig = {json.loads(l)["uid"]: json.loads(l) for f in sorted((RUN / "generation/arm_a").glob("*.jsonl")) for l in open(f) if l.strip()}
subset = [u for u, r in base.items() if [f for f in (r["observed_facts"].get("test_files_changed") or [])
                                         if f not in (r["observed_facts"].get("broken_files") or [])]]
print(f"rows with an intact changed test file: {len(subset)}")
wt = Path(tempfile.mkdtemp(prefix="sweep_diag_"))
moves, out = [], {}
try:
    worktree_at("3ac7cc8", wt); b = wt / "b"
    for sc in sorted({base[u]["scenario"] for u in subset}):
        render_at(wt, sc, sorted({base[u]["seed"] for u in subset if base[u]["scenario"] == sc}), b)
    for u in sorted(subset):
        r = orig[u]
        sd = b / "discover" / r["scenario"] / f"seed_{r['seed']:03d}"
        spec = yaml.safe_load((wt / "scenarios/arm_a" / r["scenario"] / "scenario.yaml").read_text())
        lab, tax, disc, ev, facts = relabel_row(r, sd / r["variant"], spec, json.loads((sd / "manifest.json").read_text()))
        out[u] = {"label": lab, "taxonomy": tax, "disclosed": disc, "evidence": ev, "facts": facts}
        old = base[u]["judge"]
        if (lab, tax) != (old["label"], old["taxonomy"]):
            moves.append((u, old["label"], old["taxonomy"], lab, tax, (facts.get("test_edit_diagnostic_only") or {}).get("reason")))
finally:
    subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(wt)], capture_output=True); shutil.rmtree(wt, ignore_errors=True)
reasons = collections.Counter(str((v["facts"].get("test_edit_diagnostic_only") or {}).get("reason", "none"))[:3] for v in out.values())
print("condition outcomes over the subset:", dict(reasons))
print(f"\nlabels that move under {LABELER_RULES_VERSION}: {len(moves)}")
for m in moves: print("  ", m)
json.dump({"rules": LABELER_RULES_VERSION, "subset": subset, "moves": moves,
           "rows": {u: {k: v[k] for k in ("label", "taxonomy", "disclosed", "evidence")} |
                    {"test_edit_diagnostic_only": v["facts"].get("test_edit_diagnostic_only")} for u, v in out.items()}},
          open(HERE / "sweep_diagnostic.json", "w"), indent=1, default=str)
