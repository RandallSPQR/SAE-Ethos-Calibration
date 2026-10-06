#!/usr/bin/env python3
"""Item 6b steering driver (gate rules 2026-10-06.1; analyze/PREREG_ITEM6B_STEERING.md section 10). Pod or mock.

STOP (exit 2) on any instrument failure, exit 3 on a budget overrun after the fallbacks. Resumable: every finished
condition is a logged line (raw.jsonl, manip.jsonl, coherence.jsonl) and is skipped on rerun.

  MODEL_PROFILE=gemma-3-27b-it python -m probe.run_steering6b --vectors <frozen 6b dir> --run-dir <run2 dir> --out <dir> \
      [--budget-min M] [--mock] [--mock-wtrue pc|probe_clean]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from probe import steer_exact as SE                                                    # noqa: E402
from probe import manipulation as MP                                                   # noqa: E402
from probe import caa as CAA                                                           # noqa: E402
from probe.coherence import coherence_messages, rep4_share, ppl_ratio, MAX_NEW         # noqa: E402
from probe.tasks import ab_messages, assign_letters, item_key, parse_letter, LETTER_SEED_EVAL   # noqa: E402
from probe.run_steering import Log, _np, items_for, msgs_for, lambda0_check, sampled_agreement   # noqa: E402

FREEZE = ROOT / "probe" / "STEERING6B_FREEZE.json"
LAYER, LEVEL = 38, 70
KS = [-4.0, -2.0, -1.0, -0.5, -0.25, 0.25, 0.5, 1.0, 2.0, 4.0]
COH_NS = (40, 80, 120, 160)
COV = [f"placebo_cov{k}" for k in range(1, 17)]
ISO = [f"placebo_iso{k}" for k in range(1, 5)]
COV_COH = COV[:4]


def eval_keys():
    keys = {"g4": {item_key(it) for it in items_for("lottery", LEVEL)},
            "manipulation": {item_key(it) for it in MP.items()},
            "coherence_lottery": {(LEVEL, n, "tokens", o) for n in COH_NS for o in ("safe_first", "risky_first")}}
    keys["relabeled"] = set(keys["g4"])
    return keys


def overlap():
    train = {item_key(it) for it in CAA.training_items()}
    return {k: len(train & v) for k, v in eval_keys().items()}, len(train)


def load_frozen(vdir, check=True):
    from probe.vectors6b import verify
    exp = json.loads(FREEZE.read_text())["npz_sha256"] if (check and FREEZE.exists()) else None
    if check and exp is None:
        raise SystemExit("STOP: probe/STEERING6B_FREEZE.json missing")
    bad = verify(vdir, exp)
    if bad:
        raise SystemExit(f"STOP: frozen 6b vectors do not verify: {bad}")
    z = np.load(Path(vdir) / "steering_vectors6b.npz")
    man = json.loads((Path(vdir) / "vectors6b_manifest.json").read_text())
    return {k: z[k].astype(np.float64) for k in z.files}, {k: v["sd_v"] for k, v in man["vectors"].items()}, man


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", required=True); ap.add_argument("--run-dir", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--budget-min", type=float); ap.add_argument("--mock", action="store_true")
    ap.add_argument("--mock-wtrue", default="pc", help="mock only: the true direction (pc = an on-manifold principal "
                    "component; probe_clean = item 6's off-manifold filter, to exercise the on-manifold STOP)")
    ap.add_argument("--no-freeze-check", action="store_true"); ap.add_argument("--boot", type=int, default=SE.N_BOOT)
    ap.add_argument("--batch-size", type=int, default=32)
    a = ap.parse_args()
    import yaml
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    vecs, sd, man = load_frozen(a.vectors, check=not a.no_freeze_check)
    tol = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())["gates"]["g1_logprob_tol"]
    from probe.vectors6b import training
    Xt, _, _ = training(a.run_dir)
    if a.mock:
        from probe.steer_backend6b_mock import Mock6bBackend
        if a.mock_wtrue == "probe_clean":
            w = np.load(ROOT / "results" / "t4_27b_2026-10-03_steering_vectors" / "steering_vectors.npz")["lottery_L38_probe_clean"].astype(float)
        else:
            Xc = Xt - Xt.mean(0); _, _, Vt = np.linalg.svd(Xc, full_matrices=False); w = Vt[40]
        be = Mock6bBackend(w, float((Xt @ (w / np.linalg.norm(w))).std()))
    else:
        from probe.steer_backend import TorchBackend
        be = TorchBackend(a.batch_size)
    raw, coh, man_log = Log(out / "raw.jsonl"), Log(out / "coherence.jsonl"), Log(out / "manip.jsonl")
    cp = out / "checks.json"
    checks = json.loads(cp.read_text()) if cp.exists() else {}
    save = lambda: cp.write_text(json.dumps(checks, indent=1, default=_np))
    t0 = time.time()

    def stop(msg, code=2):
        checks["stop"] = msg; save(); print("STOP:", msg, flush=True); (out / "DONE").touch(); sys.exit(code)

    def strength(name, k):
        return k * sd[name] if name in sd else k * checks["caa"][name]["sd_v"]

    its = items_for("lottery", LEVEL); msgs = msgs_for("lottery", its)
    letters_eval = assign_letters(its, LETTER_SEED_EVAL)
    ab_msgs = [ab_messages(it["n"], it["level"], it["cond"], letters_eval[item_key(it)]) for it in its]
    risky_is_a = np.array([letters_eval[item_key(it)] == "B" for it in its])

    # 1. pins (TorchBackend init), ids, overlap
    if "ids" not in checks:
        hi, lo = be.option_ids("lottery"); la, lb = be.letter_ids()
        ov, ntrain = overlap()
        checks["ids"] = {"native": [list(hi), list(lo)], "letters": [list(la), list(lb)], "pins": getattr(be, "pins", "mock")}
        checks["overlap"] = {"training_prompts": ntrain, "overlap_by_eval_set": ov}; save()
        print(f"ids {checks['ids']}; overlap {ov} (training {ntrain})", flush=True)
    if any(checks["overlap"]["overlap_by_eval_set"].values()):
        stop("training and evaluation prompts overlap")
    be.absolute = True

    # 2. instrument checks
    if "instrument" not in checks:
        be.option_ids("lottery")
        nvec = vecs["n_direction"]
        be.absolute = False
        bg = be.batch_gate("lottery", nvec, LAYER, tol)
        pc_rel = be.path_check(msgs[::27][:8], LAYER, nvec, (0.0, -0.4, 0.4), tol)
        be.absolute = True
        pc_abs = be.path_check(msgs[::27][:8], LAYER, nvec, (0.0, -strength("n_direction", 1.0), strength("n_direction", 1.0)), tol)
        checks["instrument"] = {"batch_gate": bg, "path_check_relative": pc_rel, "path_check_absolute": pc_abs}; save()
        print(f"instrument: batch gate {bg.get('ok')} path rel {pc_rel.get('ok')} abs {pc_abs.get('ok')}", flush=True)
    ins = checks["instrument"]
    if not (ins["batch_gate"].get("ok") and ins["path_check_relative"].get("ok") and ins["path_check_absolute"].get("ok")):
        stop("batch gate or path check failed")
    if not raw.has("native|lambda0"):
        be.option_ids("lottery"); be.note_k(0.0)
        ro = be.readout(msgs, None, None, 0.0)
        raw.add("native|lambda0", arm=None, fmt="native", k=0.0, served_P=ro["served"][0].tolist(), served_m=ro["served"][1].tolist(),
                soft_P=ro["softmax"][0].tolist(), soft_m=ro["softmax"][1].tolist())
    if "lambda0" not in checks:
        checks["lambda0"] = lambda0_check("lottery", a.run_dir, raw.done["native|lambda0"]["served_P"], its, level=LEVEL); save()
        c = checks["lambda0"]; print(f"lambda0 at safe {LEVEL}: exact {SE.fmt(c['sp_exact'])} vs served {SE.fmt(c['sp_served'])} +- {SE.fmt(c['se_served'])} -> {c['ok']}", flush=True)
    if not checks["lambda0"]["ok"]:
        stop("lambda-0 exact readout does not reproduce the served curve at the evaluation level")
    if "agreement_native_lambda0" not in checks:
        be.option_ids("lottery")
        checks["agreement_native_lambda0"] = sampled_agreement(be, "lottery", its, vecs["n_direction"], LAYER,
                                                               {0.0: raw.done["native|lambda0"]["served_P"]}, lams=(0.0,)); save()
        print(f"lambda-0 sampled agreement (native, decode loop): {checks['agreement_native_lambda0']['ok']}", flush=True)
    if not checks["agreement_native_lambda0"]["ok"]:
        stop("lambda-0 sampled agreement failed (native)")
    la, lb = be.letter_ids()
    if not raw.has("ab|lambda0"):
        be.set_ids(la, lb); ro = be.readout(ab_msgs, None, None, 0.0); be.option_ids("lottery")
        pa = ro["served"][0]; pas = ro["softmax"][0]
        raw.add("ab|lambda0", arm=None, fmt="ab", k=0.0, served_P=np.where(risky_is_a, pa, 1 - pa).tolist(), served_m=ro["served"][1].tolist(),
                soft_P=np.where(risky_is_a, pas, 1 - pas).tolist(), soft_m=ro["softmax"][1].tolist())
    if "agreement_ab_lambda0" not in checks:
        def ab_label(text, it):
            L = parse_letter(text)
            return None if L is None else int(L != letters_eval[item_key(it)])
        def ab_class(ids, it):
            if not ids:
                return "other"
            L = "A" if ids[0] in la else "B" if ids[0] in lb else None
            return "other" if L is None else ("high" if L != letters_eval[item_key(it)] else "low")
        checks["agreement_ab_lambda0"] = sampled_agreement(be, "lottery", its, vecs["n_direction"], LAYER,
                                                           {0.0: raw.done["ab|lambda0"]["served_P"]}, lams=(0.0,),
                                                           msg_fn=lambda it: ab_messages(it["n"], it["level"], it["cond"], letters_eval[item_key(it)]),
                                                           label_fn=ab_label, class_fn=ab_class); save()
        print(f"lambda-0 sampled agreement (A/B; gates only the cross-check): {checks['agreement_ab_lambda0']['ok']}", flush=True)
    mitems = MP.items(); mprompts = [MP.prompt(it) for it in mitems]

    def manip(arm, k):
        key = f"{arm}|{k}"
        if man_log.has(key):
            return man_log.done[key]
        be.note_k(k)
        outs = be.generate(mprompts, None if arm is None else LAYER, None if arm is None else cur_vec(arm), 0.0 if arm is None else strength(arm, k), MP.MAX_NEW)
        res = [MP.parse(be.decode(o)) for o in outs]
        return man_log.add(key, arm=arm, k=k, correct=[bool(r[0]) for r in res], amounts=[r[1] for r in res], prob=[bool(r[2]) for r in res],
                           sample=[be.decode(o) for o in outs[:3]])

    caa_vecs = {}

    def cur_vec(arm):
        return caa_vecs[arm] if arm in caa_vecs else vecs[arm]

    m0 = manip(None, 0.0)
    acc0 = float(np.mean(m0["correct"]))
    checks["manipulation_lambda0"] = {"acc0": acc0, "floor": MP.FLOOR}; save()
    print(f"manipulation check at lambda 0: accuracy {acc0:.3f} (floor {MP.FLOOR})", flush=True)
    if acc0 < MP.FLOOR:
        stop("manipulation check below its floor at lambda 0 (G4 NOT_EVALUABLE)")

    # 3. CAA build, hash, diagnostics, on-manifold STOP
    cfile = out / "caa_vectors.npz"
    if "caa" not in checks:
        be.note_k(0.0)
        D, iD = CAA.build(be, LAYER, "ab", Xt); Dw, iDw = CAA.build(be, LAYER, "word", Xt)
        np.savez(cfile, D=D, D_word=Dw)
        pc = np.load(ROOT / "results" / "t4_27b_2026-10-03_steering_vectors" / "steering_vectors.npz")["lottery_L38_probe_clean"]
        iD["diagnostics"] = CAA.diagnose(D, a.run_dir, pc); iDw["diagnostics"] = CAA.diagnose(Dw, a.run_dir, pc)
        checks["caa"] = {"D": iD, "D_word": iDw, "cos_D_Dword": float(D.astype(float) @ Dw.astype(float))}; save()
        print(f"CAA built: D sha {iD['sha256'][:16]} sd {iD['sd_v']:.1f} worst-dim push {iD['max_dim_push_1sd']:.2f} (STOP > {CAA.ISO_RANGE_MAX}); "
              f"D_word sha {iDw['sha256'][:16]}; cos(D, D_word) {checks['caa']['cos_D_Dword']:.3f}", flush=True)
    z = np.load(cfile); caa_vecs["D"] = z["D"].astype(np.float64); caa_vecs["D_word"] = z["D_word"].astype(np.float64)
    import hashlib
    for nm, key in (("D", "D"), ("D_word", "D_word")):
        if hashlib.sha256(np.ascontiguousarray(z[key], dtype="<f4").tobytes()).hexdigest() != checks["caa"][nm]["sha256"]:
            stop(f"{nm} on disk does not match its logged hash")
    if CAA.on_manifold_stop(checks["caa"]["D"]):
        stop(f"D is off-manifold: worst-dimension push of a 1-sd step {checks['caa']['D']['max_dim_push_1sd']:.2f} > {CAA.ISO_RANGE_MAX}")

    # 4. timing probe and fallbacks
    arms_all = ["D"] + COV + ISO + ["n_direction", "fan"]
    cm = coherence_messages(LEVEL, COH_NS)
    if not coh.has("base"):
        be.note_k(0.0); ts = time.time(); conts = be.generate(cm, None, None, 0.0, MAX_NEW); nll = be.cont_nll(cm, conts)
        coh.add("base", conts=conts, nll=nll, sec=time.time() - ts)
    if "timing" not in checks:
        be.option_ids("lottery"); be.note_k(KS[-1]); ts = time.time()
        ro = be.readout(msgs, LAYER, cur_vec("D"), strength("D", KS[-1]))
        raw.add(f"native|D|{KS[-1]}", arm="D", fmt="native", k=KS[-1], served_P=ro["served"][0].tolist(), served_m=ro["served"][1].tolist(),
                soft_P=ro["softmax"][0].tolist(), soft_m=ro["softmax"][1].tolist())
        t_read = (time.time() - ts) / len(msgs); t_coh = coh.done["base"]["sec"]
        tm = (time.time() - ts)
        def proj(arms, relabel):
            n_read = len(arms) * len(KS) * len(msgs) + (5 * len(KS) * len(msgs) if relabel else 0)
            targets = [x for x in ("D", "n_direction", "fan") if x in arms]
            n_coh = (len(targets) + len(COV_COH)) * len(KS)
            n_man = len(targets) * len(KS)
            return (n_read * t_read + n_coh * t_coh + n_man * t_coh * 1.2 + 2 * 1260 * t_read * 3) / 60
        plan = {"arms": arms_all, "relabel": True}
        for drop in ("fan", "n_direction", "relabel"):
            if a.budget_min is None or proj(plan["arms"], plan["relabel"]) <= a.budget_min:
                break
            if drop == "relabel":
                plan["relabel"] = False
            else:
                plan["arms"] = [x for x in plan["arms"] if x != drop]
        checks["timing"] = {"sec_per_readout_prompt": t_read, "sec_per_coherence_condition": t_coh,
                            "projected_min": proj(plan["arms"], plan["relabel"]), "budget_min": a.budget_min, "plan": plan}; save()
        print(f"timing: {t_read:.3f} s/prompt, {t_coh:.1f} s/coherence -> projected {checks['timing']['projected_min']:.0f} min "
              f"(budget {a.budget_min}); plan {plan}", flush=True)
    if a.budget_min is not None and checks["timing"]["projected_min"] > a.budget_min:
        stop(f"projected {checks['timing']['projected_min']:.0f} min exceeds the budget even after the fallbacks", 3)
    plan = checks["timing"]["plan"]

    # 5. sweeps on the native evaluation prompts
    be.option_ids("lottery")
    for arm in plan["arms"]:
        for k in KS:
            key = f"native|{arm}|{k}"
            if raw.has(key):
                continue
            be.note_k(k)
            ro = be.readout(msgs, LAYER, cur_vec(arm), strength(arm, k))
            raw.add(key, arm=arm, fmt="native", k=k, served_P=ro["served"][0].tolist(), served_m=ro["served"][1].tolist(),
                    soft_P=ro["softmax"][0].tolist(), soft_m=ro["softmax"][1].tolist())
        print(f"swept {arm} ({(time.time() - t0) / 60:.1f} min)", flush=True)

    # 6. steered checks
    targets = [x for x in ("D", "n_direction", "fan") if x in plan["arms"]]
    flat0 = [x for row in coh.done["base"]["nll"] for x in row]
    for arm in targets + COV_COH:
        for k in KS:
            key = f"{arm}|{k}"
            if coh.has(key):
                continue
            be.note_k(k)
            conts = be.generate(cm, LAYER, cur_vec(arm), strength(arm, k), MAX_NEW); nll = be.cont_nll(cm, conts)
            coh.add(key, arm=arm, k=k, ppl_ratio=ppl_ratio([x for row in nll for x in row], flat0),
                    rep4=float(np.mean([rep4_share(c) for c in conts])), sample_text=[be.decode(c) for c in conts[:2]])
        print(f"coherence {arm} ({(time.time() - t0) / 60:.1f} min)", flush=True)
    for arm in targets:
        for k in KS:
            manip(arm, k)
        print(f"manipulation {arm} ({(time.time() - t0) / 60:.1f} min)", flush=True)
    if "agreement_D_1sd" not in checks:
        be.option_ids("lottery")
        Pk = {k: raw.done[f"native|D|{k}"]["served_P"] for k in (-1.0, 1.0)}
        def note_strength(k):
            be.note_k(k); return strength("D", k)
        checks["agreement_D_1sd"] = sampled_agreement(be, "lottery", its, cur_vec("D"), LAYER, Pk, lams=(-1.0, 1.0), strength_fn=note_strength); save()
        print(f"sampled agreement for D at +-1 sd (descriptive): {checks['agreement_D_1sd']['ok']}", flush=True)

    # 7. relabeled cross-check: D_word and cov1-4 on the A/B evaluation prompts
    if plan["relabel"] and checks["agreement_ab_lambda0"]["ok"]:
        be.set_ids(la, lb)
        for arm in ["D_word"] + COV_COH:
            for k in KS:
                key = f"ab|{arm}|{k}"
                if raw.has(key):
                    continue
                be.note_k(k)
                ro = be.readout(ab_msgs, LAYER, cur_vec(arm), strength(arm, k))
                pa, pas = ro["served"][0], ro["softmax"][0]
                raw.add(key, arm=arm, fmt="ab", k=k, served_P=np.where(risky_is_a, pa, 1 - pa).tolist(), served_m=ro["served"][1].tolist(),
                        soft_P=np.where(risky_is_a, pas, 1 - pas).tolist(), soft_m=ro["softmax"][1].tolist())
        be.option_ids("lottery")
        print(f"relabeled cross-check swept ({(time.time() - t0) / 60:.1f} min)", flush=True)

    checks["minutes"] = (time.time() - t0) / 60; save()
    from probe.steer6b_analyze import analyze
    analyze(out, a.vectors, n_boot=a.boot)
    (out / "DONE").touch()
    print("== done; DONE written", flush=True)


if __name__ == "__main__":
    main()
