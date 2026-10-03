"""Tests for item 6 steering (MODEL_PROFILE=gemma-3-27b-it python -m probe.test_steering). Synthetic data with known
answers for every piece the G4 verdict rests on, plus the mock driver end to end (STOPs, resume)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

os.environ.setdefault("MODEL_PROFILE", "gemma-3-27b-it")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from probe import steer_exact as SE                                   # noqa: E402
from probe import coherence as CO                                     # noqa: E402
from probe import naturalness as NA                                   # noqa: E402
from probe import vectors as VE                                       # noqa: E402
from probe.psychometric import switching_point_soft                   # noqa: E402
from probe.run_steering import items_for                              # noqa: E402

LAMS = [-0.8, -0.6, -0.4, -0.2, -0.1, 0.0, 0.1, 0.2, 0.4, 0.6, 0.8]


def _P(items, lam, k, cell_off=None, noise=0.0, seed=0, sp0=88.0, s=8.0):
    """P(high) = sigmoid((n - sp0 + k * lam + offset_cell) / s): +lam with k > 0 lowers sp."""
    rng = np.random.default_rng(seed)
    off = cell_off or {}
    return np.array([1 / (1 + np.exp(-((it["n"] - sp0 + k * lam + off.get(it["cell"], 0.0)) / s + noise * rng.normal()))) for it in items])


def _P_by(items, target_k, placebo_ks, cell_off=None, noise=0.0):
    out = {"v": {l: _P(items, l, target_k, cell_off) for l in LAMS}}
    for j, pk in enumerate(placebo_ks):
        out[f"p{j}"] = {l: _P(items, l, pk, cell_off, noise, seed=1000 + 100 * j + int(round(10 * (l + 1)))) for l in LAMS}
    for v in out:
        out[v][0.0] = out["v"][0.0]
    return out


def run():
    res = {}
    its = items_for("lottery")
    off = {c: o for c, o in zip(sorted({it["cell"] for it in its}), (-6, -3, 0, 2, 4, 6))}
    coh_all = {l: True for l in LAMS}

    # ---- readouts: top-p can drop an option from the nucleus; the softmax sensitivity readout keeps it
    lg = np.full((1, 6), -20.0); lg[0, 0], lg[0, 1], lg[0, 2] = 5.0, 2.6, 0.0   # high dominant, low small
    r = SE.readouts(lg, (0,), (1,), T=0.8, top_p=0.95)
    res["readout: top-p cuts the low option (served P = 1), softmax keeps it (P < 1)"] = (
        abs(r["served"][0][0] - 1.0) < 1e-12 and r["softmax"][0][0] < 0.999)
    lg2 = np.zeros((1, 4)); lg2[0, 0], lg2[0, 1] = 1.0, 1.0
    r2 = SE.readouts(lg2, (0,), (1,))
    res["readout: symmetric options give P = 0.5 and mass < 1 with filler"] = abs(r2["served"][0][0] - 0.5) < 1e-9 and r2["softmax"][1][0] < 1

    # ---- switching points
    ns = np.arange(10, 181, 5); ps = 1 / (1 + np.exp(-(ns - 92.0) / 6))
    res["sp_of: logistic centred at 92 -> 92 +- 1"] = abs(SE.sp_of(ns, ps) - 92.0) < 1.0
    res["sp_of: no 0.5 crossing -> None"] = SE.sp_of(ns, np.full(len(ns), 0.2)) is None
    noisy = ps + np.where(np.arange(len(ns)) % 2, 0.08, -0.08); noisy = np.clip(noisy, 0, 1)
    res["sp_of: isotonic fit tolerates a wiggle (within 4 tokens)"] = abs(SE.sp_of(ns, noisy) - 92.0) < 4.0
    sps = switching_point_soft(ns, ps)
    res["switching_point_soft: logistic centred at 92 -> 92 +- 1 (lapse-aware fit)"] = sps["method"] == "logistic_lapse" and abs(sps["sp"] - 92) < 1

    # ---- G4 verdicts
    pk16 = [0.0] * 16
    g = SE.g4_verdict(its, _P_by(its, 60.0, pk16, off, noise=0.02), "v", [f"p{j}" for j in range(16)], LAMS, coh_all, n_boot=200)
    res["G4: a 48-token placebo-subtracted effect with 16 flat placebos -> PASS"] = g["verdict"] == "PASS" and abs(g["median_E"] + 48) < 3
    g = SE.g4_verdict(its, _P_by(its, 10.0, [8.0] * 16, off), "v", [f"p{j}" for j in range(16)], LAMS, coh_all, n_boot=200)
    res["G4: target barely above placebo-sized effects -> FAIL (criterion 1)"] = g["verdict"] == "FAIL" and not g["criteria"]["1_median_E_le_-10"]
    g = SE.g4_verdict(its, _P_by(its, -60.0, pk16, off), "v", [f"p{j}" for j in range(16)], LAMS, coh_all, n_boot=200)
    res["G4: wrong sign -> FAIL"] = g["verdict"] == "FAIL" and not g["criteria"]["2_sign_agree_ge_n-1"]
    pks = [0.0] * 15 + [70.0]
    g = SE.g4_verdict(its, _P_by(its, 60.0, pks, off), "v", [f"p{j}" for j in range(16)], LAMS, coh_all, n_boot=200)
    res["G4: one placebo moves more than the target -> FAIL (criterion 3, beats every placebo)"] = (
        g["verdict"] == "FAIL" and not g["criteria"]["3_beats_every_placebo_ge_n-1"])
    sat = _P_by(its, 1500.0, pk16, off)          # |shift| >= 150 tokens already at |lambda| = 0.1
    g = SE.g4_verdict(its, sat, "v", [f"p{j}" for j in range(16)], LAMS, coh_all, n_boot=50)
    res["G4: saturation at every |lambda| -> NOT_EVALUABLE"] = g["verdict"] == "NOT_EVALUABLE"
    coh_low = {l: abs(l) <= 0.2 for l in LAMS}
    g = SE.g4_verdict(its, _P_by(its, 60.0, pk16, off), "v", [f"p{j}" for j in range(16)], LAMS, coh_low, n_boot=100)
    res["G4: incoherent at 0.4 -> lambda* falls back to 0.2"] = g.get("lambda_star") == 0.2
    g = SE.g4_verdict(its, _P_by(its, 60.0, pk16, off), "v", [f"p{j}" for j in range(16)], LAMS, coh_all, instrument_ok=False)
    res["G4: failed instrument check -> NOT_EVALUABLE"] = g["verdict"] == "NOT_EVALUABLE"
    res["dose monotone: decreasing passes, a large reversal fails"] = (SE.monotone_ok({-0.4: 110, 0: 90, 0.4: 70})
                                                                        and not SE.monotone_ok({-0.4: 110, 0: 70, 0.4: 90}))

    # ---- per-item shifts
    P_by = _P_by(its, 60.0, [0.0, 0.0])
    pi = SE.per_item(its, P_by, "v", ["p0", "p1"], 0.4)
    res["per-item: +lambda moves P(high) up, no wrong-way items"] = pi["share_wrong_way"] == 0 and pi["quantiles_5_25_50_75_95"][-1] > 0.1

    # ---- coherence
    res["rep4: no repeats 0, a looped sequence near 1"] = CO.rep4_share(list(range(64))) == 0 and CO.rep4_share([1, 2, 3, 4] * 16) > 0.9
    res["ppl ratio: equal NLL -> 1, +ln 2 per token -> 2"] = (abs(CO.ppl_ratio([1.0] * 5, [1.0] * 5) - 1) < 1e-12
                                                              and abs(CO.ppl_ratio([1 + np.log(2)] * 5, [1.0] * 5) - 2) < 1e-9)
    lm = CO.lottery_reasoning_messages()
    res["coherence prompts: 24 neutral + 8 lottery with the reasoning instruction"] = (
        len(CO.coherence_messages()) == 32 and len(lm) == 8 and all(CO.REASON_INSTRUCTION in m[0]["content"] for m in lm)
        and all("Answer with exactly one of" not in m[0]["content"] for m in lm))

    # ---- naturalness
    rng = np.random.default_rng(1); d = 64; w = rng.normal(size=d); w /= np.linalg.norm(w)
    U = 200; Hl = rng.normal(size=(U, d)); Hh = Hl + 0.8 * w + 0.3 * rng.normal(size=(U, d))
    Xc = rng.normal(size=(300, d)); Xc -= Xc.mean(0)
    ev = NA.evaluate(Hh, Hl, np.full(U, 0.7), np.full(U, 0.5), {"aligned": w, "random": rng.normal(size=d)}, Xc, 38, n_boot=200)
    res["naturalness: aligned vector PASS, random vector FAIL"] = ev["vectors"]["aligned"]["verdict"] == "PASS" and ev["vectors"]["random"]["verdict"] == "FAIL"
    ev2 = NA.evaluate(Hh, Hl, np.full(U, 0.5), np.full(U, 0.5), {"aligned": w}, Xc, 38, n_boot=100)
    res["naturalness: personas that do not move behavior -> NOT_EVALUABLE"] = ev2["vectors"]["aligned"]["verdict"] == "NOT_EVALUABLE"
    res["personas: 4 pairs per task, verbatim count"] = all(len(v) == 4 for v in NA.PERSONAS.values())

    # ---- vectors: seeds, covariance placebo, freeze
    res["placebo seeds: deterministic and distinct per k and layer"] = (
        np.allclose(VE.placebo_iso(16, VE.iso_seed(1, 38)), VE.placebo_iso(16, VE.iso_seed(1, 38)))
        and len({VE.iso_seed(k, L) for k in range(1, 9) for L in (30, 38, 40, 46)} | {VE.cov_seed(k, L) for k in range(1, 9) for L in (30, 38, 40, 46)}) == 64)
    A = rng.normal(size=(50, 3)) @ rng.normal(size=(3, d)); A -= A.mean(0)
    pc = VE.placebo_cov(A, 5)
    proj = np.linalg.lstsq(A.T, pc, rcond=None)[0]
    res["covariance placebo lies in the activations' row space"] = np.linalg.norm(A.T @ proj - pc) < 1e-8
    t = Path(tempfile.mkdtemp())
    np.savez(t / "steering_vectors.npz", a_L1_x=np.ones(4, np.float32))
    man = {"vectors": {"a_L1_x": {"sha256": VE.sha(np.ones(4, np.float32))}}, "npz_sha256": VE.file_sha(t / "steering_vectors.npz")}
    (t / "vectors_manifest.json").write_text(json.dumps(man))
    ok_before = VE.verify(t) == []
    np.savez(t / "steering_vectors.npz", a_L1_x=np.ones(4, np.float32) * 1.0001)
    res["freeze: verify passes, then catches a tampered vector"] = ok_before and len(VE.verify(t)) >= 1

    # ---- the MoD blocker on synthetic deterministic prompts: grid-point matching can only see the surface
    rng2 = np.random.default_rng(7); dd = 24
    w_dv = rng2.normal(size=dd); w_dv /= np.linalg.norm(w_dv); w_s = rng2.normal(size=dd); w_s -= (w_s @ w_dv) * w_dv; w_s /= np.linalg.norm(w_s)
    rows = []
    for lvl in (30, 50, 100):
        for n in range(10, 181, 10):
            for o in ("safe_first", "risky_first"):
                for un in ("tokens", "points", "dollars"):
                    sv = (1.0 if o == "risky_first" else -1.0) * (1.5 if un == "tokens" else 0.5)
                    x = 2.0 * np.tanh((n / lvl - 1.7) * 2) * w_dv + sv * w_s + 0.05 * rng2.normal(size=dd)   # one activation per prompt
                    for a in range(4):                                     # 4 sampled choices per prompt, same activation
                        pr = 1 / (1 + np.exp(-((n / lvl - 1.7) * 6 + 1.2 * sv)))
                        rows.append((lvl, n, o, un, x, int(rng2.random() < pr)))
    zz = {"level": np.array([r[0] for r in rows]), "param": np.array([r[1] for r in rows]), "order": np.array([r[2] for r in rows]),
          "unit": np.array([r[3] for r in rows])}
    XX = np.stack([r[4] for r in rows]); yy = np.array([r[5] for r in rows]); trr = np.arange(len(rows))
    g_mod, _ = VE.mod_matched(XX, zz, yy, trr); f_mod, _ = VE.mod_frame_matched(XX, zz, yy, trr)
    c = lambda a_, b_: abs(float(a_ @ b_ / np.linalg.norm(a_) / np.linalg.norm(b_)))
    res["MoD dropped (ruling C), the reason reproduced: grid-point matching -> surface direction; frame matching -> stimulus/decision variable"] = (
        c(g_mod, w_s) > 0.8 and c(g_mod, w_dv) < 0.3 and c(f_mod, w_dv) > 0.9)

    # ---- descriptive: n direction from choice-homogeneous prompts (needs a run dir with baseline.json)
    td = Path(tempfile.mkdtemp()); (td / "probe" / "lottery").mkdir(parents=True)
    (td / "probe" / "lottery" / "baseline.json").write_text(json.dumps({"sp_by_level": {"50": {"sp": 100.0}}}))
    rng3 = np.random.default_rng(3); d3 = 16; w_n = rng3.normal(size=d3); w_n /= np.linalg.norm(w_n)
    w_c = rng3.normal(size=d3); w_c -= (w_c @ w_n) * w_n; w_c /= np.linalg.norm(w_c)
    ns3 = np.repeat(np.arange(10, 181, 10), 4).astype(float); lv3 = np.full(len(ns3), 50.0)
    y3 = (ns3 > 100).astype(int)
    X3 = (ns3[:, None] / 100.0) * w_n + 2.0 * (2 * y3[:, None] - 1) * w_c + 0.01 * rng3.normal(size=(len(ns3), d3))
    z3 = {"level": lv3, "param": ns3}
    dn, info = VE.n_direction_homogeneous(X3, z3, y3, "lottery", td, "below")
    res["descriptive: homogeneous-region n direction recovers the number axis, orthogonal to the choice axis"] = (
        dn is not None and abs(float(dn @ w_n)) > 0.95 and abs(float(dn @ w_c)) < 0.1 and info["grid_points"] >= 3)

    # ---- driver end to end on the mock (vectors built to a temp dir from run 2 when available)
    run2 = ROOT / "results" / "t4_27b_2026-10-02_probe_transfer_run2"
    if (run2 / "probe" / "lottery" / "activations.npz").exists():
        vd, od = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        env = {**os.environ, "MODEL_PROFILE": "gemma-3-27b-it"}
        VE.build(run2, vd, n_boot=50)
        cmd = [sys.executable, "-m", "probe.run_steering", "--vectors", str(vd), "--run-dir", str(run2), "--mock", "--no-freeze-check", "--boot", "50"]
        p = subprocess.run(cmd + ["--out", str(od / "a"), "--budget-min", "-1"], cwd=ROOT, env=env, capture_output=True, text=True)
        res["driver: projected time over budget -> STOP, exit 3"] = p.returncode == 3 and "exceeds the budget" in p.stdout
        p = subprocess.run(cmd + ["--out", str(od / "b")], cwd=ROOT, env=env, capture_output=True, text=True)
        rep = json.loads((od / "b" / "steering.json").read_text()) if (od / "b" / "steering.json").exists() else {}
        res["driver: mock end to end -> G4 PASS on the aligned probe direction, DONE written"] = (
            p.returncode == 0 and rep.get("G4", {}).get("verdict") == "PASS" and (od / "b" / "DONE").exists())
        n1 = sum(1 for _ in open(od / "b" / "raw.jsonl"))
        p = subprocess.run(cmd + ["--out", str(od / "b")], cwd=ROOT, env=env, capture_output=True, text=True)
        n2 = sum(1 for _ in open(od / "b" / "raw.jsonl"))
        res["driver: rerun resumes (no condition recomputed)"] = p.returncode == 0 and n1 == n2
        p = subprocess.run([sys.executable, "-m", "probe.run_steering", "--vectors", str(vd), "--run-dir", str(run2), "--mock",
                            "--out", str(od / "c")], cwd=ROOT, env=env, capture_output=True, text=True)
        res["driver: unfrozen vectors (no matching STEERING_FREEZE) -> STOP"] = p.returncode != 0 and "STOP" in (p.stdout + p.stderr)
    return res


if __name__ == "__main__":
    r = run()
    for k, v in r.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in r.items() if not v]
    print(f"\n{len(r) - len(bad)}/{len(r)} passed")
    sys.exit(1 if bad else 0)
