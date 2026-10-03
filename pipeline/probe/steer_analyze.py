#!/usr/bin/env python3
"""Item 6 analysis (gate rules 2026-10-03.1). Reads a run_steering output dir; writes steering.json and STEERING.md.
Runs on the pod at the end of the driver and offline after copy-back (the committed numbers are the offline ones).

  python -m probe.steer_analyze --out <run_steering out dir> --vectors <frozen vectors dir>
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from probe import steer_exact as SE                         # noqa: E402
from probe.coherence import PPL_RATIO_MAX, REP4_MAX, MASS_MIN   # noqa: E402

READOUTS = (("served", "served_P", "served_m"), ("softmax", "soft_P", "soft_m"))


def _rows(p):
    return [json.loads(l) for l in open(p) if l.strip()] if Path(p).exists() else []


def analyze(out_dir, vectors_dir, n_boot=SE.N_BOOT):
    import modelcfg
    from probe.run_steering import items_for
    out = Path(out_dir)
    raw = {r["key"]: r for r in _rows(out / "raw.jsonl")}
    coh = {r["key"]: r for r in _rows(out / "coherence.jsonl")}
    checks = json.loads((out / "checks.json").read_text())
    man = json.loads((Path(vectors_dir) / "vectors_manifest.json").read_text())
    lams_all = [float(x) for x in modelcfg.probe_cfg()["lambda_sweep"]]
    inst = checks.get("instrument", {})
    instrument_ok = bool(inst.get("batch_gate", {}).get("ok") and inst.get("path_check", {}).get("ok")
                         and all(v.get("ok") for k, v in checks.items() if k.startswith("lambda0_"))
                         and checks.get("sampled_agreement", {}).get("ok"))
    rep = {"rules": SE.RULES, "instrument_ok": instrument_ok, "checks": {k: v for k, v in checks.items() if k != "stop"},
           "stop": checks.get("stop"), "sites": {}}
    sites = sorted(man["sites"].values(), key=lambda s: ["primary", "secondary", "exploratory"].index(s["role"]))
    for s in sites:
        t, L = s["task"], s["layer"]; site = f"{t}_L{L}"
        its = items_for(t)
        vec_names = sorted({r["vec"] for r in raw.values() if r.get("site") == site})
        placebos = [v for v in vec_names if v.startswith("placebo")]
        targets = [v for v in ("probe_clean", "mod_clean") if v in vec_names]
        z0 = raw.get(f"{t}|lambda0")
        if z0 is None or not targets:
            rep["sites"][site] = {"status": "not run"}; continue
        srep = {"role": s["role"], "targets": targets, "n_placebos": len(placebos), "naturalness": checks.get(f"naturalness_{t}", {}).get(str(L)),
                "transfer": s["transfer"], "mod_steered": checks.get("mod_steered", {}).get(site), "by_target": {}}
        for tv in targets:
            lams = [l for l in lams_all if l == 0 or (f"{site}|{tv}|{l}" in raw and all(f"{site}|{p}|{l}" in raw for p in placebos))]
            coherent, coh_detail = {}, {}
            for l in lams:
                r = z0 if l == 0 else raw[f"{site}|{tv}|{l}"]
                mass = float(np.mean(r["served_m"]))
                c = None if l == 0 else coh.get(f"{site}|{tv}|{l}")
                ok = mass >= MASS_MIN and (l == 0 or (c is not None and c["ppl_ratio"] <= PPL_RATIO_MAX and c["rep4"] <= REP4_MAX))
                coherent[l] = bool(ok)
                coh_detail[str(l)] = {"mass": mass, "ppl_ratio": None if c is None else c["ppl_ratio"], "rep4": None if c is None else c["rep4"], "coherent": bool(ok)}
            trep = {"coherence": coh_detail}
            for name, pk, _ in READOUTS:
                P_by = {v: {l: np.asarray((z0 if l == 0 else raw[f"{site}|{v}|{l}"])[pk], float) for l in lams} for v in [tv] + placebos}
                g = SE.g4_verdict(its, P_by, tv, placebos, lams, coherent, instrument_ok, n_boot)
                lam_star = g.get("lambda_star")
                items_l = [l for l in lams if l != 0 and coherent.get(l)]
                g["per_item"] = {str(l): SE.per_item(its, P_by, tv, placebos, l) for l in items_l}
                g["pooled_sp_by_lambda"] = {str(l): {"target": SE.pooled_sp(its, P_by[tv][l]),
                                                     "placebo_mean": (float(np.mean([x for x in (SE.pooled_sp(its, P_by[p][l]) for p in placebos) if x is not None]))
                                                                      if any(SE.pooled_sp(its, P_by[p][l]) is not None for p in placebos) else None)}
                                            for l in lams}
                g["lambda_star_used"] = lam_star
                trep[name] = g
            srep["by_target"][tv] = trep
        rep["sites"][site] = srep
    prim = sites[0]; pk = f"{prim['task']}_L{prim['layer']}"
    g4 = rep["sites"].get(pk, {}).get("by_target", {}).get("probe_clean", {}).get("served", {})
    rep["G4"] = {"site": pk, "vector": "probe_clean", "readout": "served", "verdict": g4.get("verdict", "NOT_EVALUABLE"),
                 "reason": g4.get("reason"), "criteria": g4.get("criteria"),
                 "sensitivity_softmax_verdict": rep["sites"].get(pk, {}).get("by_target", {}).get("probe_clean", {}).get("softmax", {}).get("verdict")}
    (out / "steering.json").write_text(json.dumps(rep, indent=1, default=lambda o: None if o is None else float(o)))
    (out / "STEERING.md").write_text(render(rep))
    print(render(rep))
    return rep


def render(rep):
    f = SE.fmt
    L = [f"# Item 6 steering (gate rules {rep['rules']})", ""]
    if rep.get("stop"):
        L += [f"**STOPPED:** {rep['stop']}", ""]
    g = rep["G4"]
    L += [f"**G4 ({g['site']}, {g['vector']}, served readout): {g['verdict']}**"
          + (f" ({g['reason']})" if g.get("reason") else "") + f"; sensitivity (untruncated softmax): {g['sensitivity_softmax_verdict']}", ""]
    if g.get("criteria"):
        L += ["| criterion | met |", "|---|---|"] + [f"| {k} | {v} |" for k, v in g["criteria"].items()] + [""]
    L += [f"Instrument checks all passed: {rep['instrument_ok']}", ""]
    for site, s in rep["sites"].items():
        if "by_target" not in s:
            L += [f"## {site}: {s.get('status')}", ""]; continue
        L += [f"## {site} ({s['role']}; {s['n_placebos']} placebos; MoD steered: {s['mod_steered']})", ""]
        nat = s.get("naturalness") or {}
        if nat:
            L += [f"Naturalness: personas valid {nat.get('valid')} (dP {f(nat.get('behavior_shift_mean'), 3)}); null p99 |cos| "
                  f"{f(nat.get('null_p99_abs_cos'), 3)}; " + "; ".join(f"{k} cos {f(v['cos'], 3)} [{f(v['ci95'][0], 3)}, {f(v['ci95'][1], 3)}] {v['verdict']}"
                                                                  for k, v in nat.get("vectors", {}).items()), ""]
        L += ["Transfer (2026-10-02.1): " + "; ".join(f"{k} {f(v['agent_auroc'], 3)} {v['verdict']}" for k, v in s["transfer"].items()), ""]
        L += ["| vector | readout | lambda* | E per cell | median E | pooled E [95 %] | sign agree | beats every placebo | verdict |",
              "|---|---|---|---|---|---|---|---|---|"]
        for tv, tr in s["by_target"].items():
            for name in ("served", "softmax"):
                r = tr[name]
                E = r.get("E_by_cell") or {}
                ci = r.get("pooled_E_ci95") or [None, None]
                L.append(f"| {tv} | {name} | {f(r.get('lambda_star'), 2)} | " + ", ".join(f"{c.split('/')[0][:5]}/{c.split('/')[-1][:3]} {f(e)}" for c, e in E.items())
                         + f" | {f(r.get('median_E'))} | {f(r.get('pooled_E'))} [{f(ci[0])}, {f(ci[1])}] | {r.get('sign_agree', '-')} | "
                         f"{r.get('beats_every_placebo', '-')} | {r['verdict']}{' (' + r['reason'] + ')' if r.get('reason') else ''} |")
        L += ["", "Dose curve, pooled sp (served): lambda: target / placebo mean / coherent"]
        for tv, tr in s["by_target"].items():
            ps = tr["served"]["pooled_sp_by_lambda"]
            L.append(f"- {tv}: " + "  ".join(f"{k}: {f(v['target'])}/{f(v['placebo_mean'])}/{'y' if tr['coherence'][k]['coherent'] else 'n'}"
                                         for k, v in sorted(ps.items(), key=lambda kv: float(kv[0]))))
        L += ["", "Per-item shifts at lambda* (served; placebo-subtracted d_i): quantiles 5/25/50/75/95, moved, wrong way, at-target-side mean"]
        for tv, tr in s["by_target"].items():
            ls = tr["served"].get("lambda_star")
            for sign in (1, -1):
                if ls is None:
                    continue
                k = str(sign * ls) if str(sign * ls) in tr["served"]["per_item"] else None
                if k:
                    p = tr["served"]["per_item"][k]
                    L.append(f"- {tv} lambda {k}: [{', '.join(f(x, 3) for x in p['quantiles_5_25_50_75_95'])}], moved {f(p['share_moved'], 2)}, "
                             f"wrong way {f(p['share_wrong_way'], 2)}, at-target-side ({p['n_at_target_side']}) {f(p['mean_d_at_target_side'], 3)}")
        L.append("")
    L += ["Reading: steering is judged on behavior minus placebo, never on probe readout. The served readout decides; the untruncated "
          "softmax is a sensitivity readout (top-p can drop an option from the nucleus and put a step in the dose curve). Naturalness "
          "of the lottery personas includes lexical priming: personas 1, 3 and 4 share vocabulary with the option text."]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--vectors", required=True); ap.add_argument("--boot", type=int, default=SE.N_BOOT)
    a = ap.parse_args()
    analyze(a.out, a.vectors, a.boot)


if __name__ == "__main__":
    main()
