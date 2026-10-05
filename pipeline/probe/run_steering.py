#!/usr/bin/env python3
"""Item 6 steering driver (gate rules 2026-10-03.1; analyze/PREREG_ITEM6_STEERING.md). Pod or mock.

Order (a failed instrument check STOPs with exit 2; a budget overrun STOPs with exit 3):
  0  frozen vectors verified against probe/STEERING_FREEZE.json (sha256)
  1  option first-token ids == run 2's observed ids
  2  batch gate (probe.batch_gate) and the HF-hook path check (prefill logits and GPU readout)
  3  lambda = 0 check per task: exact pooled sp (weighted to run 2's cell mix) within 2 SE of run 2's served sp
  4  timing probe -> projected minutes; STOP if over --budget-min
  5  naturalness per task (probe_clean; reported, gates nothing: MoD was dropped from item 6)
  6  per site, in priority order: sweeps (every vector x every lambda != 0, exact readout, both readouts kept),
     sampled agreement (primary site, probe_clean, lambda -0.4 / 0 / +0.4), coherence
  7  analysis (probe.steer_analyze) -> STEERING.md, steering.json; DONE
Resumable: every finished condition is a line in raw.jsonl / coherence.jsonl and is skipped on rerun.

  MODEL_PROFILE=gemma-3-27b-it python -m probe.run_steering --vectors <frozen dir> --run-dir <run2 dir> --out <dir> \
      [--budget-min M] [--mock]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from probe import steer_exact as SE                       # noqa: E402
from probe.coherence import coherence_messages, rep4_share, ppl_ratio, MAX_NEW   # noqa: E402
from probe.naturalness import PERSONAS, persona_messages, evaluate as nat_evaluate   # noqa: E402
from probe.tasks import TASKS, UNITS, messages, parse_choice, label   # noqa: E402

FREEZE = ROOT / "probe" / "STEERING_FREEZE.json"
AGREE_LAMS = (-0.4, 0.0, 0.4)
AGENTS = 6
PLACEBO_COHERENCE = ("placebo_iso1", "placebo_iso2", "placebo_cov1", "placebo_cov2")   # placebo coherence: primary site only


def items_for(task):
    if task == "lottery":
        cells = [(o, u) for o in ("safe_first", "risky_first") for u in UNITS]
        its = [{"n": n, "cell": f"{o}/{u}", "cond": {"unit": u, "order": o}, "level": 50} for n in TASKS[task]["grid"] for o, u in cells]
    else:
        its = [{"n": n, "cell": u, "cond": {"unit": u, "order": "safe_first"}, "level": None} for n in TASKS[task]["grid"] for u in UNITS]
    return its


def msgs_for(task, its):
    return [messages(task, it["n"], it["level"], it["cond"]) for it in its]


def seed_for(cell_idx, a, n):
    return 1_000_000 + 10_000 * cell_idx + 1_000 * a + int(n)


def _np(o):
    """numpy scalars / arrays -> JSON (token ids from a numpy backend are int64)."""
    return o.tolist() if hasattr(o, "tolist") else str(o)


class Log:
    def __init__(self, path):
        self.path = Path(path); self.done = {}
        if self.path.exists():
            for l in open(self.path):
                if l.strip():
                    r = json.loads(l); self.done[r["key"]] = r

    def has(self, key):
        return key in self.done

    def add(self, key, **kw):
        r = {"key": key, **kw}; self.done[key] = r
        with open(self.path, "a") as fh:
            fh.write(json.dumps(r, default=_np) + "\n")
        return r


def load_vectors(vdir, check_freeze=True):
    from probe.vectors import verify
    exp = json.loads(FREEZE.read_text())["npz_sha256"] if (check_freeze and FREEZE.exists()) else None
    if check_freeze and exp is None:
        raise SystemExit("STOP: probe/STEERING_FREEZE.json missing: vectors are not frozen")
    bad = verify(vdir, exp)
    if bad:
        raise SystemExit(f"STOP: frozen vectors do not verify: {bad}")
    z = np.load(Path(vdir) / "steering_vectors.npz")
    man = json.loads((Path(vdir) / "vectors_manifest.json").read_text())
    return {k: z[k] for k in z.files if not k.endswith("__layer")}, man


def lambda0_check(task, run_dir, P0, its):
    """Same estimator as probe.calibrate.lambda0_checksum (the lapse-aware logistic, psychometric.switching_point): run 2's
    served reference-level labels vs the exact lambda-0 probabilities weighted to run 2's (n, cell) trial counts
    (switching_point_soft). |gap| <= 2 x the served sp's within-grid-point bootstrap SE; the exact side has no sampling
    noise. The PAV crossings of both are reported beside it (descriptive)."""
    from probe.psychometric import switching_point, switching_point_soft
    rows = [json.loads(l) for l in open(Path(run_dir) / "probe" / task / "trials.jsonl") if l.strip()]
    ref = TASKS[task]["reference_level"]
    rows = [r for r in rows if r["level"] == ref and r["label"] is not None]
    cellname = (lambda c: f"{c['order']}/{c['unit']}") if task == "lottery" else (lambda c: c["unit"])
    cnt = {}
    for r in rows:
        k = (r["param"], cellname(r["cond"])); cnt[k] = cnt.get(k, 0) + 1
    w = [cnt.get((it["n"], it["cell"]), 0) for it in its]
    ex = switching_point_soft([it["n"] for it in its], P0, w)
    ns = np.array([r["param"] for r in rows], float); ys = [int(r["label"]) for r in rows]
    sv = switching_point(ns.tolist(), ys)
    rng = np.random.default_rng(SE.BOOT_SEED); b = []
    ya = np.array(ys)
    for _ in range(500):
        ix = np.concatenate([rng.choice(np.where(ns == v)[0], size=(ns == v).sum()) for v in np.unique(ns)])
        s = switching_point(ns[ix].tolist(), ya[ix].tolist())["sp"]
        if s is not None:
            b.append(s)
    se = float(np.std(b)) if len(b) > 1 else None
    gap = None if (ex["sp"] is None or sv["sp"] is None) else abs(ex["sp"] - sv["sp"])
    ok = gap is not None and se is not None and gap <= 2 * max(se, 1e-9)
    return {"task": task, "sp_exact": ex["sp"], "method_exact": ex["method"], "sp_served": sv["sp"], "method_served": sv["method"],
            "se_served": se, "gap": gap, "ok": bool(ok),
            "pav_exact": SE.sp_of([it["n"] for it in its], P0, w), "pav_served": SE.sp_of(ns, np.array(ys, float))}


def sampled_agreement(be, task, its, vec, layer, P_exact_by_lam, lams=AGREE_LAMS):
    """Steered sampled answers (6 agents per item) vs the exact readout: first token decides the parsed answer in
    >= 99 % of parseable answers, and per cell the sampled sp lies within 2 SE of the exact served sp in >= n-1 cells."""
    cells = sorted({it["cell"] for it in its}); cidx = {c: i for i, c in enumerate(cells)}
    rep = {"by_lambda": {}}
    ok = True
    for lam in lams:
        rows = [(it, a) for it in its for a in range(AGENTS)]
        msgs = [messages(task, it["n"], it["level"], it["cond"]) for it, _ in rows]
        seeds = [seed_for(cidx[it["cell"]], a, it["n"]) for it, a in rows]
        outs = be.generate(msgs, layer, vec, lam, 6, sample={"seeds": seeds})
        labs, agree, n_par = [], 0, 0
        for (it, _), ids in zip(rows, outs):
            ch = parse_choice(task, be.decode(ids), it["cond"]); y = label(task, ch); labs.append(y)
            ft = be.first_token_class(task, ids)
            if y is not None and ft in ("high", "low"):
                n_par += 1; agree += (ft == ch)
        cons = agree / n_par if n_par else None            # no parseable answers: the first-token property cannot be shown
        cell_ok = 0; detail = {}
        for c in cells:
            ix = [i for i, (it, _) in enumerate(rows) if it["cell"] == c and labs[i] is not None]
            ns = np.array([rows[i][0]["n"] for i in ix], float); ys = np.array([labs[i] for i in ix], float)
            ex_ix = [i for i, it in enumerate(its) if it["cell"] == c]
            spe = SE.sp_of([its[i]["n"] for i in ex_ix], np.asarray(P_exact_by_lam[lam])[ex_ix])
            if len(ix) == 0:
                # no parseable sampled answer in the cell (the steered model stopped answering): agreement cannot be
                # shown, so the cell does not agree (2026-10-05 pod: probe_clean at L38 collapsed the option mass)
                cell_ok += 0; detail[c] = {"sp_sampled": None, "se": None, "sp_exact": spe, "n_parseable": 0, "agree": False}
                continue
            sps = SE.sp_of(ns, ys)
            rng = np.random.default_rng(SE.BOOT_SEED); b = []
            for _ in range(200):
                jx = np.concatenate([rng.choice(np.where(ns == v)[0], size=(ns == v).sum()) for v in np.unique(ns)])
                s = SE.sp_of(ns[jx], ys[jx])
                if s is not None:
                    b.append(s)
            se = max(float(np.std(b)) if len(b) > 1 else 0.0, 1.0)
            agree_c = (sps is None and spe is None) or (sps is not None and spe is not None and abs(sps - spe) <= 2 * se)
            cell_ok += bool(agree_c); detail[c] = {"sp_sampled": sps, "se": se, "sp_exact": spe, "n_parseable": int(len(ix)), "agree": bool(agree_c)}
        lam_ok = cons is not None and cons >= 0.99 and cell_ok >= len(cells) - 1
        ok = ok and lam_ok
        rep["by_lambda"][str(lam)] = {"first_token_consistency": cons, "n_parseable": n_par, "n": len(rows),
                                      "cells_agree": cell_ok, "cells": detail, "ok": bool(lam_ok)}
    rep["ok"] = bool(ok)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", required=True); ap.add_argument("--run-dir", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--budget-min", type=float, default=None); ap.add_argument("--mock", action="store_true")
    ap.add_argument("--no-freeze-check", action="store_true", help="mock tests only")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--boot", type=int, default=SE.N_BOOT, help="bootstrap draws in the analysis (tests use fewer)")
    a = ap.parse_args()
    import modelcfg
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    vecs, man = load_vectors(a.vectors, check_freeze=not a.no_freeze_check)
    lams = [float(x) for x in modelcfg.probe_cfg()["lambda_sweep"]]
    sites = sorted(man["sites"].values(), key=lambda s: ["primary", "secondary", "exploratory"].index(s["role"]))
    import yaml
    tol = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())["gates"]["g1_logprob_tol"]
    if a.mock:
        from probe.steer_backend import MockBackend
        be = MockBackend(vecs, [(s["task"], s["layer"]) for s in sites])
    else:
        from probe.steer_backend import TorchBackend
        be = TorchBackend(a.batch_size)
    raw = Log(out / "raw.jsonl"); coh = Log(out / "coherence.jsonl")
    checks_p = out / "checks.json"
    checks = json.loads(checks_p.read_text()) if checks_p.exists() else {}
    save = lambda: checks_p.write_text(json.dumps(checks, indent=1, default=_np))
    t0 = time.time()
    prim = sites[0]; pv = vecs[f"{prim['task']}_L{prim['layer']}_probe_clean"]

    def stop(msg, code=2):
        checks["stop"] = msg; save(); print("STOP:", msg, flush=True); (out / "DONE").touch(); sys.exit(code)

    # 1-2 instrument checks
    if "instrument" not in checks:
        ids = {t: [list(x) for x in be.option_ids(t)] for t in sorted({s["task"] for s in sites})}
        bg = be.batch_gate(prim["task"], pv, prim["layer"], tol)
        be.option_ids(prim["task"])
        pc = be.path_check(msgs_for(prim["task"], items_for(prim["task"])[::27][:8]), prim["layer"], pv, (0.0, -0.4, 0.4), tol)
        checks["instrument"] = {"option_ids": ids, "batch_gate": bg, "path_check": pc}; save()
        print(f"instrument: batch gate {bg.get('ok')}  path check {pc.get('ok')}", flush=True)
    if not (checks["instrument"]["batch_gate"].get("ok") and checks["instrument"]["path_check"].get("ok")):
        stop("batch gate or HF-hook path check failed")

    # 3 lambda = 0 per task (also the lambda-0 readout every vector shares)
    tasks = sorted({s["task"] for s in sites})
    for t in tasks:
        k = f"{t}|lambda0"
        if not raw.has(k):
            be.option_ids(t); its = items_for(t)
            ro = be.readout(msgs_for(t, its), None, None, 0.0)
            raw.add(k, task=t, site=None, vec=None, lam=0.0, served_P=ro["served"][0].tolist(), served_m=ro["served"][1].tolist(),
                    soft_P=ro["softmax"][0].tolist(), soft_m=ro["softmax"][1].tolist(), sec=time.time() - t0)
        if f"lambda0_{t}" not in checks:
            checks[f"lambda0_{t}"] = lambda0_check(t, a.run_dir, raw.done[k]["served_P"], items_for(t)); save()
            c = checks[f"lambda0_{t}"]
            print(f"[{t}] lambda0: exact sp {SE.fmt(c['sp_exact'])} vs served {SE.fmt(c['sp_served'])} +- {SE.fmt(c['se_served'])} -> {'OK' if c['ok'] else 'FAIL'}", flush=True)
        if not checks[f"lambda0_{t}"]["ok"]:
            stop(f"lambda-0 exact readout does not reproduce the served curve ({t})")

    # 3b (item 6b (4)): the FIRST generation on the pod is lambda-0 sampled agreement through the KV-cached decode loop: the
    # steered sampler at lambda 0 must reproduce the exact readout (first token decides >= 99 %, per-cell sp within 2 SE)
    # before any sweep spends pod time. STOP on failure.
    if "agreement_lambda0" not in checks:
        be.option_ids(prim["task"]); its0 = items_for(prim["task"])
        P0 = raw.done[f"{prim['task']}|lambda0"]["served_P"]
        rep0 = sampled_agreement(be, prim["task"], its0, pv, prim["layer"], {0.0: P0}, lams=(0.0,))
        checks["agreement_lambda0"] = rep0; save()
        print(f"[{prim['task']}] lambda-0 sampled agreement (KV decode loop): ok={rep0['ok']}", flush=True)
    if not checks["agreement_lambda0"]["ok"]:
        stop("lambda-0 sampled agreement failed: the decode loop does not reproduce the exact readout")

    # plan of conditions
    def steered_vecs(s):
        """probe_clean and the site's placebos (MoD dropped from item 6, ruling C)."""
        return ["probe_clean"], [k.split(f"_L{s['layer']}_", 1)[1] for k in man["vectors"] if k.startswith(f"{s['task']}_L{s['layer']}_placebo")]

    # 4 timing probe: one steered condition at the primary site + the lambda-0 coherence pass
    cm = coherence_messages()
    if not coh.has("base"):
        ts = time.time(); conts = be.generate(cm, None, None, 0.0, MAX_NEW); tg = time.time() - ts
        ts = time.time(); nll = be.cont_nll(cm, conts); tn = time.time() - ts
        coh.add("base", conts=conts, nll=nll, sec_gen=tg, sec_nll=tn)
    if "timing" not in checks:
        be.option_ids(prim["task"]); its = items_for(prim["task"]); ts = time.time()
        k = f"{prim['task']}_L{prim['layer']}|probe_clean|{lams[-1]}"
        ro = be.readout(msgs_for(prim["task"], its), prim["layer"], pv, lams[-1])
        raw.add(k, task=prim["task"], site=f"{prim['task']}_L{prim['layer']}", vec="probe_clean", lam=lams[-1],
                served_P=ro["served"][0].tolist(), served_m=ro["served"][1].tolist(), soft_P=ro["softmax"][0].tolist(), soft_m=ro["softmax"][1].tolist())
        t_read = (time.time() - ts) / len(its)
        t_coh = coh.done["base"]["sec_gen"] + coh.done["base"]["sec_nll"]
        n_read = n_coh = 0
        for s in sites:
            nv, npl = steered_vecs(s)
            n_read += (len(nv) + len(npl)) * (len(lams) - 1) * len(items_for(s["task"]))
            n_coh += (len(nv) + (len(PLACEBO_COHERENCE) if s is prim else 0)) * (len(lams) - 1)
        n_nat = sum(2 * 4 * len(items_for(t)) for t in tasks)
        n_agree = len(AGREE_LAMS) * AGENTS * len(items_for(prim["task"]))
        proj = (n_read * t_read + n_coh * t_coh + n_nat * t_read + n_agree * t_read * 3) / 60
        checks["timing"] = {"sec_per_readout_prompt": t_read, "sec_per_coherence_condition": t_coh, "readout_prompts": n_read,
                            "coherence_conditions": n_coh, "projected_min": proj, "budget_min": a.budget_min}; save()
        print(f"timing: {t_read:.3f} s/prompt readout, {t_coh:.1f} s/coherence condition -> projected {proj:.0f} min "
              f"(budget {a.budget_min})", flush=True)
    if a.budget_min is not None and checks["timing"]["projected_min"] > a.budget_min:
        stop(f"projected {checks['timing']['projected_min']:.0f} min exceeds the budget {a.budget_min:.0f} min", 3)

    # 5 naturalness per task (unsteered persona prompts; residuals at the task's site layers)
    for t in tasks:
        if f"naturalness_{t}" in checks:
            continue
        be.option_ids(t); its = items_for(t)
        layers = [s["layer"] for s in sites if s["task"] == t]
        hi_m, lo_m = [], []
        for it in its:
            for hi, lo in PERSONAS[t]:
                hi_m.append(persona_messages(t, it["n"], it["level"], it["cond"], hi))
                lo_m.append(persona_messages(t, it["n"], it["level"], it["cond"], lo))
        Hh, rh = be.resid_readout(hi_m, layers); Hl, rl = be.resid_readout(lo_m, layers)
        zn = np.load(Path(a.run_dir) / "probe" / t / "activations.npz")
        from probe.vectors import training_split
        tr = training_split(t, zn)
        res = {}
        for L in layers:
            Xc = zn[f"X_{L}"][tr].astype(np.float64); Xc = Xc - Xc.mean(0)
            res[str(L)] = nat_evaluate(Hh[L], Hl[L], rh["served"][0], rl["served"][0],
                                       {"probe_clean": vecs[f"{t}_L{L}_probe_clean"]}, Xc, L)
            r = res[str(L)]
            print(f"[{t} L{L}] naturalness: valid {r['valid']} (dP {r['behavior_shift_mean']:+.3f})  "
                  + "  ".join(f"{k} cos {v['cos']:.3f} {v['verdict']}" for k, v in r["vectors"].items()) + f"  null p99 {r['null_p99_abs_cos']:.3f}", flush=True)
        checks[f"naturalness_{t}"] = res; save()

    # 6 per site
    for s in sites:
        t, L = s["task"], s["layer"]; site = f"{t}_L{L}"
        nv, npl = steered_vecs(s)
        be.option_ids(t); its = items_for(t); msgs = msgs_for(t, its)
        for vname in nv + npl:
            v = vecs[f"{site}_{vname}"]
            for lam in lams:
                if lam == 0:
                    continue
                k = f"{site}|{vname}|{lam}"
                if raw.has(k):
                    continue
                ro = be.readout(msgs, L, v, lam)
                raw.add(k, task=t, site=site, vec=vname, lam=lam, served_P=ro["served"][0].tolist(), served_m=ro["served"][1].tolist(),
                        soft_P=ro["softmax"][0].tolist(), soft_m=ro["softmax"][1].tolist(), sec=time.time() - t0)
            print(f"[{site}] {vname} swept ({(time.time() - t0) / 60:.1f} min)", flush=True)
        if s is prim and "sampled_agreement" not in checks:
            P_by = {lam: (raw.done[f"{t}|lambda0"]["served_P"] if lam == 0 else raw.done[f"{site}|probe_clean|{lam}"]["served_P"]) for lam in AGREE_LAMS}
            checks["sampled_agreement"] = sampled_agreement(be, t, its, vecs[f"{site}_probe_clean"], L, P_by); save()
            print(f"[{site}] sampled agreement ok={checks['sampled_agreement']['ok']}", flush=True)
        base = coh.done["base"]
        flat0 = [x for row in base["nll"] for x in row]
        for vname in nv + ([p for p in PLACEBO_COHERENCE if p in npl] if s is prim else []):
            v = vecs[f"{site}_{vname}"]
            for lam in lams:
                if lam == 0:
                    continue
                k = f"{site}|{vname}|{lam}"
                if coh.has(k):
                    continue
                conts = be.generate(cm, L, v, lam, MAX_NEW)
                nll = be.cont_nll(cm, conts)
                coh.add(k, site=site, vec=vname, lam=lam, ppl_ratio=ppl_ratio([x for row in nll for x in row], flat0),
                        rep4=float(np.mean([rep4_share(c) for c in conts])), sample_text=[be.decode(c) for c in conts[:2]] + [be.decode(conts[-1])])
            print(f"[{site}] {vname} coherence done ({(time.time() - t0) / 60:.1f} min)", flush=True)

    checks["minutes"] = (time.time() - t0) / 60; save()
    from probe.steer_analyze import analyze
    analyze(out, vectors_dir=a.vectors, n_boot=a.boot)
    (out / "DONE").touch()
    print("== done; DONE written", flush=True)


if __name__ == "__main__":
    main()
