"""Tests for item 6b (MODEL_PROFILE=gemma-3-27b-it python -m probe.test_steering6b): letters, formats, parsers,
disjointness, the manipulation rule, eligibility, the CAA projection and counterbalancing, the on-manifold STOP, and the mock
driver end to end (PASS, on-manifold STOP, budget STOP, resume, freeze refusal)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

os.environ.setdefault("MODEL_PROFILE", "gemma-3-27b-it")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from probe import manipulation as MP                                                       # noqa: E402
from probe import caa as CAA                                                               # noqa: E402
from probe import steer6b_analyze as AN                                                    # noqa: E402
from probe import steer_exact as SE                                                        # noqa: E402
from probe.tasks import ab_messages, assign_letters, item_key, parse_letter, LETTER_SEED_TRAIN, LETTER_SEED_EVAL   # noqa: E402
from probe.run_steering import items_for                                                   # noqa: E402
from probe.run_steering6b import overlap, eval_keys                                        # noqa: E402

V6B = ROOT / "results" / "t4_27b_2026-10-06_item6b_vectors"
RUN2 = ROOT / "results" / "t4_27b_2026-10-02_probe_transfer_run2"


def _fake_rows(arm="D", bad=None):
    """raw / coh / man rows where everything is coherent and passes, except the (kind, k) in `bad`."""
    raw, coh, man = {}, {}, {}
    man["None|0.0"] = {"correct": [True] * 108}
    for k in AN.KS:
        for a in [arm] + AN.COV:
            m = 0.99
            if bad and bad[0] == "placebo_mass" and a in AN.COV[: bad[2]] and abs(k) == bad[1]:
                m = 0.5
            if bad and bad[0] == "mass" and a == arm and k == bad[1]:
                m = 0.5
            raw[f"native|{a}|{k}"] = {"served_m": [m] * 210}
        coh[f"{arm}|{k}"] = {"ppl_ratio": 1.1, "rep4": 0.0}
        corr = [True] * 108
        if bad and bad[0] == "manip" and k == bad[1]:
            corr = [True] * 90 + [False] * 18
        man[f"{arm}|{k}"] = {"correct": corr}
    return raw, coh, man


def run():
    res = {}
    tr = CAA.training_items(); ev = items_for("lottery", 70)
    lt = assign_letters(tr, LETTER_SEED_TRAIN); le = assign_letters(ev, LETTER_SEED_EVAL)
    res["letters: exactly half Safe = A (training 315/630, evaluation 105/210)"] = (
        sum(v == "A" for v in lt.values()) == 315 and sum(v == "A" for v in le.values()) == 105)
    strata = {}
    for it in tr:
        strata.setdefault((it["level"], it["cond"]["unit"], it["cond"]["order"]), []).append(lt[item_key(it)])
    res["letters: every stratum within one of half; deterministic; train and eval seeds differ"] = (
        all(abs(sum(x == "A" for x in v) - len(v) / 2) <= 0.5 for v in strata.values())
        and assign_letters(tr, LETTER_SEED_TRAIN) == lt and assign_letters(ev, LETTER_SEED_TRAIN) != le)
    m = ab_messages(90, 70, {"unit": "points", "order": "risky_first"}, "A")[0]["content"]
    res["A/B format verbatim (risky line first for risky_first; safe = A; the A/B instruction)"] = (
        m.split("\n")[3] == "Option B: 50% chance of 90 points, 50% chance of 0 points." and m.split("\n")[4] == "Option A: Guaranteed 70 points."
        and m.endswith("Answer with exactly one of: A, B.") and "Safe Option" not in m)
    res["parse_letter: A, B, **A**, Option B, lowercase; C / prose -> None"] = (
        [parse_letter(t) for t in ("A", "B\n", "**A**", "Option B", "a")] == ["A", "B", "A", "B", "A"]
        and parse_letter("C") is None and parse_letter("Both are fine") is None)
    ov, ntrain = overlap()
    res["disjointness: 630 training prompts, overlap 0 with every evaluation set"] = ntrain == 630 and not any(ov.values())
    train_keys = {item_key(it) for it in tr}; bad = dict(eval_keys()); bad["g4"] = set(bad["g4"]) | {next(iter(train_keys))}
    res["disjointness: an injected training key is detected"] = len(train_keys & bad["g4"]) == 1

    # manipulation
    ok = [MP.parse(t)[0] for t in ("The guaranteed amount is 70 tokens and the chance of winning is 50%.",
                                   "Safe: 70 points. Risky: half chance of 120 points.",
                                   "The sure thing gives 70 dollars; the gamble pays with probability 0.5.")]
    no = [MP.parse(t)[0] for t in ("The guaranteed amount is 50 tokens and the chance is 50%.",
                                   "The guaranteed amount is 70 tokens.", "It is safe to say I prefer the gamble, 50% odds.")]
    res["manipulation parser: 3 correct statements accepted, 3 wrong ones rejected"] = all(ok) and not any(no)
    res["manipulation prompts: N = 108 at safe 70, the stated instruction"] = (
        len(MP.items()) == 108 and all(it["level"] == 70 for it in MP.items()) and MP.INSTRUCTION in MP.prompt(MP.items()[0])[0]["content"])
    c0 = [True] * 100 + [False] * 8
    res["manipulation rule: equal accuracy PASS; a 10-point drop FAIL; unsteered below 0.80 NOT_EVALUABLE"] = (
        MP.verdict(c0, c0)["verdict"] == "PASS" and MP.verdict([True] * 89 + [False] * 19, c0)["verdict"] == "FAIL"
        and MP.verdict(c0, [True] * 80 + [False] * 28)["verdict"] == "NOT_EVALUABLE")

    # eligibility
    el, _ = AN.eligibility("D", *_fake_rows())
    res["eligibility: all conditions met -> every |k| eligible"] = all(el.values())
    el, _ = AN.eligibility("D", *_fake_rows(bad=("mass", -1.0)))
    res["eligibility: target incoherent at -1 -> +-1 ineligible, +-0.5 still eligible"] = (not el[1.0]) and (not el[-1.0]) and el[0.5]
    el, _ = AN.eligibility("D", *_fake_rows(bad=("manip", 2.0)))
    res["eligibility: manipulation fails at +2 -> +-2 ineligible"] = not el[2.0] and el[1.0]
    el, _ = AN.eligibility("D", *_fake_rows(bad=("placebo_mass", 0.5, 5)))
    res["eligibility: 11/16 placebos intact at +-0.5 -> ineligible; 12/16 -> eligible"] = (
        not el[0.5] and AN.eligibility("D", *_fake_rows(bad=("placebo_mass", 0.5, 4)))[0][0.5])
    its = items_for("lottery", 70)
    P = lambda k: np.array([1 / (1 + np.exp(-((it["n"] - 100 + k * 12) / 8))) for it in its])
    Pb = {"D": {l: P(l) for l in AN.LAMS}}
    Pb.update({p: {l: P(0.0) for l in AN.LAMS} for p in AN.COV})
    g = SE.g4_verdict(its, Pb, "D", AN.COV, AN.LAMS, {l: True for l in AN.LAMS}, True, 100, pref=AN.K_PREF)
    res["k*: the widest eligible k <= 2 (+-4 is never k*)"] = g.get("lambda_star") == 2.0 and g["verdict"] == "PASS"

    # CAA projection and counterbalancing (mock answer residuals)
    from probe.steer_backend6b_mock import Mock6bBackend
    rng = np.random.default_rng(5); d = 64; w = rng.normal(size=d)
    be = Mock6bBackend(w, 1.0)
    Xt = rng.normal(size=(200, d))
    D, info = CAA.build(be, 38, "ab", Xt)
    res["CAA: counterbalanced letters cancel the letter direction (removed share ~0) and D recovers w"] = (
        info["removed_share"] < 1e-6 and abs(float(D @ (w / np.linalg.norm(w)))) > 0.999 and info["n_safe_is_A"] == 315)
    raw = 4 * (w / np.linalg.norm(w)) + 6 * be.u_letter
    proj, _ = CAA.project_out(raw, [be.u_letter])
    res["CAA projection: an uncancelled letter component is removed exactly"] = abs(float(proj @ be.u_letter)) < 1e-9
    res["on-manifold STOP: push 3.0 > 2.93 stops, 2.0 does not"] = CAA.on_manifold_stop({"max_dim_push_1sd": 3.0}) and not CAA.on_manifold_stop({"max_dim_push_1sd": 2.0})

    # driver end to end (mock)
    if (RUN2 / "probe" / "lottery" / "activations.npz").exists():
        env = {**os.environ, "MODEL_PROFILE": "gemma-3-27b-it"}
        od = Path(tempfile.mkdtemp())
        base = [sys.executable, "-m", "probe.run_steering6b", "--vectors", str(V6B), "--run-dir", str(RUN2), "--mock", "--boot", "50"]
        p = subprocess.run(base + ["--out", str(od / "a")], cwd=ROOT, env=env, capture_output=True, text=True)
        rep = json.loads((od / "a" / "steering6b.json").read_text()) if (od / "a" / "steering6b.json").exists() else {}
        res["driver: mock end to end -> G4 PASS on an on-manifold true direction, overlap 0, DONE"] = (
            p.returncode == 0 and rep.get("G4", {}).get("verdict") == "PASS" and not any(rep["overlap"]["overlap_by_eval_set"].values())
            and (od / "a" / "DONE").exists())
        n1 = sum(1 for _ in open(od / "a" / "raw.jsonl"))
        p = subprocess.run(base + ["--out", str(od / "a")], cwd=ROOT, env=env, capture_output=True, text=True)
        res["driver: rerun resumes (no condition recomputed)"] = p.returncode == 0 and n1 == sum(1 for _ in open(od / "a" / "raw.jsonl"))
        p = subprocess.run(base + ["--out", str(od / "b"), "--mock-wtrue", "probe_clean"], cwd=ROOT, env=env, capture_output=True, text=True)
        res["driver: an off-manifold D (item 6's filter as the true direction) -> on-manifold STOP before the sweep"] = (
            p.returncode == 2 and "off-manifold" in p.stdout and not (od / "b" / "coherence.jsonl").exists()
            or (p.returncode == 2 and "off-manifold" in p.stdout and sum(1 for l in open(od / "b" / "raw.jsonl")) <= 2))
        p = subprocess.run(base + ["--out", str(od / "c"), "--budget-min", "-1"], cwd=ROOT, env=env, capture_output=True, text=True)
        res["driver: over budget after every fallback -> STOP, exit 3"] = p.returncode == 3 and "exceeds the budget" in p.stdout
        vt = Path(tempfile.mkdtemp()) / "v"; shutil.copytree(V6B, vt)
        zt = dict(np.load(vt / "steering_vectors6b.npz")); zt["placebo_cov1"] = zt["placebo_cov1"] * np.float32(1.0001)
        np.savez(vt / "steering_vectors6b.npz", **zt)
        p = subprocess.run([sys.executable, "-m", "probe.run_steering6b", "--vectors", str(vt), "--run-dir", str(RUN2), "--mock",
                            "--out", str(od / "d")], cwd=ROOT, env=env, capture_output=True, text=True)
        res["driver: a tampered frozen vector -> STOP"] = p.returncode != 0 and "STOP" in (p.stdout + p.stderr)
    return res


if __name__ == "__main__":
    r = run()
    for k, v in r.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in r.items() if not v]
    print(f"\n{len(r) - len(bad)}/{len(r)} passed")
    sys.exit(1 if bad else 0)
