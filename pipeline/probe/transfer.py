#!/usr/bin/env python3
"""Probe-regime transfer check (queue item 5; rule pre-registered as gate rules 2026-10-02.1, Randall, before the pod).

For each probe layer and task: fit the probe on the NATIVE regime only (probe/<task>/activations.npz, the training split
probe.train uses: every safe level except the held-out one; C by 5-fold CV on that split, per layer), then score the
AGENT-regime trials (probe_agent/<task>/activations.npz: the same items under the harness's agent template, labeled by
the model's own choice in that regime) by AUROC, with a 95 % bootstrap interval resampling grid points (param x level)
as clusters. Both directions are scored: the raw probe and the surface-cleaned one (probe.train.orthogonalize).

  PASS at (task, layer)   agent-regime AUROC >= 0.70 AND its 95 % bootstrap lower bound > 0.50
  FAIL                    otherwise: the probe is NOT used at that layer for that task (Randall's rule)
  NOT_EVALUABLE           the agent regime has < 10 trials of either class (no AUROC to estimate)
The cleaned direction is the one used downstream (G9 / steering), so its verdict is the one that decides use; the raw
verdict is reported beside it. Native held-out AUROC (the held-out safe level) is reported as the reference.

  python -m probe.transfer --run-dir runs/<run_id> [--boot 2000]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AUROC_MIN, LOWER_MIN, MIN_CLASS = 0.70, 0.50, 10
RULES = "2026-10-02.1"


def auroc(score, y):
    """Mann-Whitney AUROC with ties counted half."""
    score, y = np.asarray(score, float), np.asarray(y, int)
    pos, neg = score[y == 1], score[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order)); allv = np.concatenate([pos, neg])[order]
    i = 0
    while i < len(allv):                                   # average ranks over ties
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1
        i = j + 1
    rp = ranks[:len(pos)].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def cluster_boot(score, y, clusters, n_boot, seed=0):
    rng = np.random.default_rng(seed)
    ids = np.unique(clusters); idx = {c: np.where(clusters == c)[0] for c in ids}
    out = []
    for _ in range(n_boot):
        take = np.concatenate([idx[c] for c in rng.choice(ids, size=len(ids), replace=True)])
        a = auroc(score[take], y[take])
        if a == a:
            out.append(a)
    return (float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))) if out else (float("nan"), float("nan"))


def verdict(a, lo, y):
    if min(int((y == 1).sum()), int((y == 0).sum())) < MIN_CLASS:
        return "NOT_EVALUABLE"
    return "PASS" if (a >= AUROC_MIN and lo > LOWER_MIN) else "FAIL"


def run(run_dir, n_boot=2000):
    import modelcfg
    from probe.tasks import TASKS
    from probe.train import fit_logistic, cv_score, surface_directions, orthogonalize, fit_bias
    pc = modelcfg.probe_cfg()
    layers = [int(x) for x in pc["layer_candidates"]]
    key = "Xfirst" if pc.get("position") == "first_answer_token" else "X"
    rep = {"rules": RULES, "criterion": {"auroc_min": AUROC_MIN, "boot_lower_min": LOWER_MIN, "min_per_class": MIN_CLASS,
                                         "decides_use": "cleaned direction"}, "tasks": {}}
    for task in pc["tasks"]:
        nd, ad = Path(run_dir) / "probe" / task, Path(run_dir) / "probe_agent" / task
        if not (nd / "activations.npz").exists() or not (ad / "activations.npz").exists():
            rep["tasks"][task] = {"status": "missing activations (native or agent)"}
            continue
        zn, za = np.load(nd / "activations.npz"), np.load(ad / "activations.npz")
        yn, ya = zn["y"].astype(float), za["y"].astype(int)
        ho_level = TASKS[task].get("heldout_level")
        lv = zn["level"]
        tr = np.where(lv != ho_level)[0] if ho_level is not None else np.arange(len(yn))
        ho = np.where(lv == ho_level)[0] if ho_level is not None else np.array([], int)
        clusters = np.array([f"{l}:{p}" for l, p in zip(za["level"], za["param"])])
        per = {}
        for L in layers:
            X, Xa = zn[f"{key}_{L}"].astype(np.float64), za[f"{key}_{L}"].astype(np.float64)
            C = max(pc["c_grid"], key=lambda c: cv_score(X[tr], yn[tr], float(c)))
            mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6
            w, b = fit_logistic((X[tr] - mu) / sd, yn[tr], float(C))
            w_raw = w / sd; w_raw /= (np.linalg.norm(w_raw) + 1e-12)
            w_clean, _ = orthogonalize(w_raw, surface_directions(X, zn, tr, yn) if "order" in zn.files else {})
            w_clean /= (np.linalg.norm(w_clean) + 1e-12)
            res = {"C": float(C)}
            for name, direc in (("raw", w_raw), ("clean", w_clean)):
                s_agent = Xa @ direc
                a = auroc(s_agent, ya); lo, hi = cluster_boot(s_agent, ya, clusters, n_boot)
                nat = auroc(X[ho] @ direc, yn[ho].astype(int)) if len(ho) else None
                res[name] = {"agent_auroc": a, "agent_ci95": [lo, hi], "verdict": verdict(a, lo, ya), "native_heldout_auroc": nat}
            per[str(L)] = res
        rep["tasks"][task] = {"n_native_train": int(len(tr)), "n_native_heldout": int(len(ho)), "n_agent": int(len(ya)),
                              "agent_class_balance": float(ya.mean()) if len(ya) else None, "per_layer": per,
                              "usable_layers": [L for L, r in per.items() if r["clean"]["verdict"] == "PASS"]}
    out = Path(run_dir) / "probe_agent" / "transfer.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1))
    lines = [f"# Probe-regime transfer (gate rules {RULES}): native-trained probe, agent-regime test", "",
             f"PASS = agent AUROC >= {AUROC_MIN} and 95 % cluster-bootstrap lower bound > {LOWER_MIN}; the CLEANED direction "
             "decides use; NOT_EVALUABLE = < 10 agent trials of a class.", "",
             "| task | layer | native held-out AUROC (clean) | agent AUROC clean [95 %] | verdict (clean) | agent AUROC raw [95 %] | verdict (raw) |",
             "|---|---|---|---|---|---|---|"]
    for task, t in rep["tasks"].items():
        if "per_layer" not in t:
            lines.append(f"| {task} | - | - | - | {t['status']} | - | - |"); continue
        for L, r in t["per_layer"].items():
            c, rw = r["clean"], r["raw"]
            f = lambda x: "-" if x is None or x != x else f"{x:.3f}"
            lines.append(f"| {task} | {L} | {f(c['native_heldout_auroc'])} | {f(c['agent_auroc'])} [{f(c['agent_ci95'][0])}, "
                         f"{f(c['agent_ci95'][1])}] | **{c['verdict']}** | {f(rw['agent_auroc'])} [{f(rw['agent_ci95'][0])}, "
                         f"{f(rw['agent_ci95'][1])}] | {rw['verdict']} |")
        lines.append(f"\n{task}: usable layers (cleaned PASS): {t['usable_layers'] or 'none'}; agent n = {t['n_agent']}, "
                     f"class balance {t['agent_class_balance']:.2f}\n" if t["agent_class_balance"] is not None else "")
    (out.parent / "TRANSFER.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    run(a.run_dir, a.boot)


if __name__ == "__main__":
    main()
