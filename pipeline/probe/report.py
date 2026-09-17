#!/usr/bin/env python3
"""Reconciliation report for a probe run (rules 2026-09-16.3): prints, next to the held-out accuracy, the
ceiling that label noise imposes on it, and decomposes the psychometric plateau by surface condition.

  python -m probe.report --run-dir runs/<run_id>

Ceiling = mean over held-out trials of max(p, 1-p) where p is the label rate of the trial's cell. Two
cells: by grid point (what a probe could reach if activations were identical across seeds) and by
(grid point, order, unit) (the prompts actually differ by surface condition, so the honest ceiling).
Run 2: by-grid 0.87, by-cell 1.00 at the held-out level; the 'lapse' was an order x unit effect, not noise.
Writes probe/<task>/report.json and adds `ceiling_*` to probe.json.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from probe.psychometric import switching_point   # noqa: E402
from probe.tasks import TASKS                    # noqa: E402


def ceiling(rows, key):
    by = collections.defaultdict(list)
    for r in rows:
        by[key(r)].append(r["label"])
    rate = {k: float(np.mean(v)) for k, v in by.items()}
    return float(np.mean([max(rate[key(r)], 1 - rate[key(r)]) for r in rows]))


def report_task(task, run_dir):
    d = Path(run_dir) / "probe" / task
    rows = [json.loads(l) for l in open(d / "trials.jsonl") if l.strip()]
    rows = [r for r in rows if r["label"] is not None]
    t = TASKS[task]
    out = {"task": task, "levels": {}}
    for lv in t["levels"]:
        rs = [r for r in rows if r.get("level") == lv]
        if not rs:
            continue
        thr = (1.6 * lv) if (lv is not None and task == "lottery") else None
        hi = [r for r in rs if thr is not None and r["param"] >= thr] or rs
        by_cond = collections.defaultdict(list)
        for r in hi:
            by_cond[f"{r['cond']['order']}/{r['cond']['unit']}"].append(r["label"])
        sp_cond = {}
        for cname in sorted({f"{r['cond']['order']}/{r['cond']['unit']}" for r in rs}):
            cr = [r for r in rs if f"{r['cond']['order']}/{r['cond']['unit']}" == cname]
            s = switching_point([r["param"] for r in cr], [r["label"] for r in cr])
            sp_cond[cname] = {"sp": s["sp"], "method": s["method"], "n": len(cr)}
        out["levels"][str(lv)] = {
            "ceiling_by_grid": ceiling(rs, lambda r: r["param"]),
            "ceiling_by_cell": ceiling(rs, lambda r: (r["param"], r["cond"]["order"], r["cond"]["unit"])),
            "p_high_above_switch_by_cond": {k: float(np.mean(v)) for k, v in sorted(by_cond.items())},
            "sp_by_cond": sp_cond,
        }
    for key in ("order", "unit"):
        dd = collections.defaultdict(list)
        for r in rows:
            if r.get("level") is not None and r["param"] >= 1.6 * r["level"]:
                dd[r["cond"][key]].append(r["label"])
        out[f"p_high_above_switch_by_{key}"] = {k: float(np.mean(v)) for k, v in dd.items()}
    pj = d / "probe.json"
    if pj.exists():
        p = json.loads(pj.read_text())
        ho = t.get("heldout_level")
        lvl = out["levels"].get(str(ho))
        if lvl:
            p["ceiling_by_grid"], p["ceiling_by_cell"] = lvl["ceiling_by_grid"], lvl["ceiling_by_cell"]
            pj.write_text(json.dumps(p, indent=2))
            out["heldout"] = {"level": ho, "heldout_acc": p["heldout_acc"], **{k: lvl[k] for k in ("ceiling_by_grid", "ceiling_by_cell")}}
            print(f"[{task}] held-out level {ho}: acc={p['heldout_acc']:.3f}  ceiling_by_grid={lvl['ceiling_by_grid']:.3f}  "
                  f"ceiling_by_cell={lvl['ceiling_by_cell']:.3f}")
    for lv, v in out["levels"].items():
        print(f"   level {lv}: P(high) above switch by cond {{{', '.join(f'{k}:{x:.2f}' for k, x in v['p_high_above_switch_by_cond'].items())}}}")
    (d / "report.json").write_text(json.dumps(out, indent=2))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--tasks", default=None)
    a = ap.parse_args()
    import yaml
    pc = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())["probe"]
    for t in (a.tasks.split(",") if a.tasks else pc["tasks"]):
        if (Path(a.run_dir) / "probe" / t / "trials.jsonl").exists():
            report_task(t, a.run_dir)


if __name__ == "__main__":
    main()
