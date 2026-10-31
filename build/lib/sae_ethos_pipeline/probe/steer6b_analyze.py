#!/usr/bin/env python3
"""Item 6b analysis (gate rules 2026-10-06.1; PREREG_ITEM6B_STEERING.md sections 6-9). Reads a run_steering6b output
dir; writes steering6b.json and STEERING6B.md. Runs on the pod at the end and offline after copy-back.

  python -m probe.steer6b_analyze --out <dir> --vectors <frozen 6b dir>
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from probe import steer_exact as SE                         # noqa: E402
from probe import manipulation as MP                        # noqa: E402
from probe.coherence import PPL_RATIO_MAX, REP4_MAX, MASS_MIN   # noqa: E402

KS = [-4.0, -2.0, -1.0, -0.5, -0.25, 0.25, 0.5, 1.0, 2.0, 4.0]
LAMS = sorted(KS + [0.0])
COV = [f"placebo_cov{k}" for k in range(1, 17)]
ISO = [f"placebo_iso{k}" for k in range(1, 5)]
PLACEBO_INTACT_MIN, K_PREF = 12, 2.0


def _rows(p):
    return {json.loads(l)["key"]: json.loads(l) for l in open(p) if l.strip()} if Path(p).exists() else {}


def eligibility(arm, raw, coh, man):
    """{k: bool} for one target arm: +-k eligible iff the target is coherent at both signs, the manipulation check passes
    at both signs, and >= 12 of the 16 covariance-matched placebos keep served mass >= 0.95 at both signs."""
    m0 = man.get("None|0.0")
    def coherent(k):
        r = raw.get(f"native|{arm}|{k}"); c = coh.get(f"{arm}|{k}")
        return bool(r and c and np.mean(r["served_m"]) >= MASS_MIN and c["ppl_ratio"] <= PPL_RATIO_MAX and c["rep4"] <= REP4_MAX)
    def manip(k):
        m = man.get(f"{arm}|{k}")
        return bool(m and m0 and MP.verdict(m["correct"], m0["correct"])["verdict"] == "PASS")
    def intact(k):
        return sum(1 for p in COV if all((raw.get(f"native|{p}|{s}") or {}).get("served_m") is not None
                                         and np.mean(raw[f"native|{p}|{s}"]["served_m"]) >= MASS_MIN for s in (k, -k))) >= PLACEBO_INTACT_MIN
    el = {0.0: True}
    detail = {}
    for k in sorted({abs(x) for x in KS}):
        parts = {"coherent": coherent(k) and coherent(-k), "manipulation": manip(k) and manip(-k), "placebos_intact": intact(k)}
        el[k] = el[-k] = all(parts.values()); detail[str(k)] = parts
    return el, detail


def P_by(raw, fmt, arms, key="served_P"):
    out = {}
    for a in arms:
        d = {0.0: np.asarray(raw[f"{fmt}|lambda0"][key], float)}
        for k in KS:
            r = raw.get(f"{fmt}|{a}|{k}")
            if r is None:
                return None
            d[k] = np.asarray(r[key], float)
        out[a] = d
    return out


def analyze(out_dir, vectors_dir, n_boot=SE.N_BOOT):
    from probe.run_steering import items_for
    out = Path(out_dir)
    raw, coh, man = _rows(out / "raw.jsonl"), _rows(out / "coherence.jsonl"), _rows(out / "manip.jsonl")
    checks = json.loads((out / "checks.json").read_text())
    its = items_for("lottery", 70)
    ins = checks.get("instrument", {})
    instrument_ok = bool(ins.get("batch_gate", {}).get("ok") and ins.get("path_check_relative", {}).get("ok")
                         and ins.get("path_check_absolute", {}).get("ok") and checks.get("lambda0", {}).get("ok")
                         and checks.get("agreement_native_lambda0", {}).get("ok")
                         and (checks.get("manipulation_lambda0", {}).get("acc0") or 0) >= MP.FLOOR
                         and not any((checks.get("overlap") or {}).get("overlap_by_eval_set", {"x": 1}).values()))
    rep = {"rules": "2026-10-06.1", "instrument_ok": instrument_ok, "stop": checks.get("stop"), "caa": checks.get("caa"),
           "overlap": checks.get("overlap"), "arms": {}}
    for arm in ("D", "n_direction", "fan"):
        if raw.get(f"native|{arm}|{KS[0]}") is None:
            continue
        el, ed = eligibility(arm, raw, coh, man)
        arm_rep = {"eligibility": ed}
        for name, key in (("served", "served_P"), ("softmax", "soft_P")):
            Pc = P_by(raw, "native", [arm] + COV, key)
            if Pc is None:
                arm_rep[name] = {"verdict": "NOT_EVALUABLE", "reason": "sweep incomplete"}; continue
            g = SE.g4_verdict(its, Pc, arm, COV, LAMS, el, instrument_ok, n_boot, pref=K_PREF)
            ks = g.get("lambda_star")
            g["per_item"] = {str(s * ks): SE.per_item(its, Pc, arm, COV, s * ks) for s in (1, -1)} if ks else {}
            Pi = P_by(raw, "native", [arm] + ISO, key)
            if Pi is not None and ks:
                tv, pv, E = SE.effects_at({v: {l: SE.cell_sps(its, Pi[v][l]) for l in (ks, -ks)} for v in [arm] + ISO}, arm, ISO, ks, -ks)
                vals = [e for e in E.values() if e is not None]
                g["vs_isotropic"] = {"pooled_E": float(np.mean(vals)) if vals else None, "E_by_cell": E}
            arm_rep[name] = g
        arm_rep["manipulation"] = {str(k): (lambda m: None if m is None else {"acc": float(np.mean(m["correct"])),
                                                                             "mean_stated_amount": (float(np.mean([x for x in m["amounts"] if x is not None])) if any(x is not None for x in m["amounts"]) else None),
                                                                             **MP.verdict(m["correct"], man["None|0.0"]["correct"])})(man.get(f"{arm}|{k}")) for k in KS}
        arm_rep["role"] = "G4 (sole confirmatory test)" if arm == "D" else "descriptive"
        rep["arms"][arm] = arm_rep
    g4 = (rep["arms"].get("D") or {}).get("served") or {"verdict": "NOT_EVALUABLE", "reason": checks.get("stop") or "D not swept"}
    rep["G4"] = {"verdict": g4.get("verdict"), "reason": g4.get("reason"), "k_star": g4.get("lambda_star"), "criteria": g4.get("criteria"),
                 "pooled_E": g4.get("pooled_E"), "pooled_E_ci95": g4.get("pooled_E_ci95"), "median_E": g4.get("median_E"),
                 "sensitivity_softmax": ((rep["arms"].get("D") or {}).get("softmax") or {}).get("verdict")}
    # relabeling cross-check (descriptive)
    xc = {"evaluable": False}
    ks = rep["G4"]["k_star"]
    Pab = P_by(raw, "ab", ["D_word"] + COV[:4]) if raw.get("ab|lambda0") else None
    if not (checks.get("agreement_ab_lambda0") or {}).get("ok"):
        xc["reason"] = "A/B first-token property not shown at lambda 0"
    elif Pab is None:
        xc["reason"] = "relabeled sweep not run"
    elif ks is None:
        xc["reason"] = "G4 has no k*"
    else:
        sps = {v: {l: SE.cell_sps(its, Pab[v][l]) for l in (ks, -ks)} for v in ["D_word"] + COV[:4]}
        tv, pv, E = SE.effects_at(sps, "D_word", COV[:4], ks, -ks)
        vals = [e for e in E.values() if e is not None]
        lo, hi, nb = SE._boot_pooled_E(its, Pab, "D_word", COV[:4], ks, -ks, n_boot)
        pooled = float(np.mean(vals)) if len(vals) == len(E) else None
        g4p = rep["G4"]["pooled_E"]
        holds = (pooled is not None and lo is not None and (hi < 0 or lo > 0) and g4p is not None
                 and np.sign(pooled) == np.sign(g4p) and abs(pooled) >= 0.5 * abs(g4p))
        xc = {"evaluable": True, "k_star": ks, "pooled_E": pooled, "pooled_E_ci95": [lo, hi], "E_by_cell": E, "holds": bool(holds),
              "reading": ("steers the choice across answer formats" if (holds and rep["G4"]["verdict"] == "PASS") else "format dependence reported")}
    rep["relabeling_cross_check"] = xc
    (out / "steering6b.json").write_text(json.dumps(rep, indent=1, default=lambda o: None if o is None else (o.tolist() if hasattr(o, "tolist") else float(o))))
    (out / "STEERING6B.md").write_text(render(rep))
    print(render(rep))
    return rep


def render(rep):
    f = SE.fmt
    L = ["# Item 6b steering (gate rules 2026-10-06.1)", ""]
    if rep.get("stop"):
        L += [f"**STOPPED:** {rep['stop']}", ""]
    g = rep["G4"]
    L += [f"**G4 (the sole confirmatory test: D at lottery L38, served readout): {g['verdict']}**"
          + (f" ({g['reason']})" if g.get("reason") else "") + f"; k* = {g.get('k_star')} sd; pooled E {f(g.get('pooled_E'))} "
          f"[{f((g.get('pooled_E_ci95') or [None, None])[0])}, {f((g.get('pooled_E_ci95') or [None, None])[1])}]; median E {f(g.get('median_E'))}; "
          f"softmax sensitivity {g.get('sensitivity_softmax')}", ""]
    if g.get("criteria"):
        L += ["| criterion | met |", "|---|---|"] + [f"| {k} | {v} |" for k, v in g["criteria"].items()] + [""]
    c = rep.get("caa") or {}
    if c:
        for nm in ("D", "D_word"):
            i = c.get(nm) or {}
            L.append(f"- {nm}: sha {str(i.get('sha256'))[:16]}, sd_v {f(i.get('sd_v'))}, worst-dim push of a 1-sd step {f(i.get('max_dim_push_1sd'), 2)} "
                     f"(STOP > 2.93), low-variance share {f(i.get('low_variance_share'), 3)}, token-direction share removed {f(i.get('removed_share'), 3)}")
        L += [f"- cos(D, D_word) {f(c.get('cos_D_Dword'), 3)}", ""]
    L += [f"Overlap of the training prompts with every evaluation set: {(rep.get('overlap') or {}).get('overlap_by_eval_set')}", ""]
    for arm, r in rep["arms"].items():
        L += [f"## {arm} ({r['role']})", "", "Eligibility by |k|: " + "; ".join(f"{k}: " + ",".join(n for n, v in d.items() if not v) if not all(d.values()) else f"{k}: eligible"
                                                                          for k, d in r["eligibility"].items()), ""]
        for name in ("served", "softmax"):
            s = r.get(name) or {}
            ci = s.get("pooled_E_ci95") or [None, None]
            L.append(f"- {name}: {s.get('verdict')}{' (' + s['reason'] + ')' if s.get('reason') else ''}; k* {s.get('lambda_star')}; median E "
                     f"{f(s.get('median_E'))}; pooled E {f(s.get('pooled_E'))} [{f(ci[0])}, {f(ci[1])}]; vs isotropic pooled "
                     f"{f((s.get('vs_isotropic') or {}).get('pooled_E'))}" + ("" if arm == "D" else " (descriptive)"))
        L.append("- manipulation (acc / mean stated amount / verdict) by k: " + "  ".join(
            f"{k}: {f(m['acc'], 2)}/{f(m['mean_stated_amount'])}/{m['verdict']}" for k, m in r["manipulation"].items() if m))
        L.append("")
    x = rep["relabeling_cross_check"]
    L += ["## Relabeling cross-check (descriptive)", "",
          (f"D_word on A/B prompts at k* {x['k_star']}: pooled E {f(x['pooled_E'])} [{f(x['pooled_E_ci95'][0])}, {f(x['pooled_E_ci95'][1])}]; "
           f"holds {x['holds']} -> {x['reading']}") if x.get("evaluable") else f"not evaluable: {x.get('reason')}", "",
          "G4 is an instrument gate: a PASS shows steering works in this pipeline, not that D is a risk-preference variable."]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--vectors", required=True); ap.add_argument("--boot", type=int, default=SE.N_BOOT)
    a = ap.parse_args()
    analyze(a.out, a.vectors, a.boot)


if __name__ == "__main__":
    main()
