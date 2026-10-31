"""G4 steering_known_answer: if we can't move an easy behavior by steering a known vector, we can't
interpret a null on a hard one. Reproduce a published effect (a persona vector shifting an obvious
readout) and require a monotone-ish dose-response with |effect| over the sweep >= threshold. Real run
reads features/steering_report.json; fixture proves the effect-size + monotonicity logic.
Rules 2026-09-16.2: the readout must sit at a LIVE decision point (a prompt where the readout event has
non-negligible baseline probability); a readout at the floor (first run: P~1e-7 after "Write something.")
cannot show an absolute effect regardless of the direction's causal power. The report records the prompt
and per-strength greedy samples so the effect is inspectable. See gates/CHANGELOG.md.
Rules 2026-10-03.1 (27B, item 6; analyze/PREREG_ITEM6_STEERING.md): G4 is the PLACEBO-SUBTRACTED steering test of the
cleaned lottery probe direction at layer 38 (exact first-token readout, 16 norm-matched placebos at every strength,
coherence per strength, five criteria in probe.steer_exact.g4_verdict). When <run>/steering/steering.json exists, its G4
verdict is the gate's; the legacy readout curve applies otherwise. G4 is an instrument gate: a PASS shows steering works
in this pipeline, not that the direction is a risk-preference variable."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg, GATE_RULES_VERSION, NOT_EVALUABLE

NAME = "G4_steering_known_answer"
NEEDS_GPU = True


def effect(curve):
    """curve: {strength: readout}. Effect = readout(max+) - readout(max-)."""
    ks = sorted(curve, key=float)
    return curve[ks[-1]] - curve[ks[0]]


def coherent(curve, dip_frac=0.10):
    """Monotone in the tested range, tolerating single-step reversals no larger than dip_frac of the total
    effect (rules 2026-09-16.2 amendment: fp32 run 2 dipped 0.003 at one step of a 0.116 effect — noise,
    not a reversal). A dip larger than that is a real non-monotonicity and fails."""
    ks = sorted(curve, key=float)
    vals = [curve[k] for k in ks]
    tol = dip_frac * abs(vals[-1] - vals[0]) + 1e-6
    incr = all(y >= x - tol for x, y in zip(vals, vals[1:]))
    decr = all(y <= x + tol for x, y in zip(vals, vals[1:]))
    return incr or decr


def run(cfg, paths):
    st = Path(paths["features"]).parent / "steering" / "steering.json"
    if st.exists():                                    # rules 2026-10-03.1
        g = json.loads(st.read_text())["G4"]
        res = GateResult(NAME, g["verdict"] == "PASS", {"rules": "2026-10-03.1", "site": g["site"], "verdict": g["verdict"],
                                                       "criteria": g.get("criteria"), "reason": g.get("reason"),
                                                       "sensitivity_softmax": g.get("sensitivity_softmax_verdict")})
        if g["verdict"] == "NOT_EVALUABLE":
            res.status = NOT_EVALUABLE
        return res
    thr = load_run_cfg()["g4_steering_effect_min_abs"]
    p = Path(paths["features"]) / "steering_report.json"
    if not p.exists():
        return GateResult(NAME, False, {"error": "features/steering_report.json missing"})
    rep = json.loads(p.read_text())
    curve = {float(k): v for k, v in rep["curve"].items()}
    e = effect(curve)
    ok = abs(e) >= thr and coherent(curve)
    base = curve.get(0.0)
    return GateResult(NAME, ok, {"effect": round(e, 3), "threshold": thr, "monotone": coherent(curve),
                                 "baseline": None if base is None else round(base, 4),
                                 "readout": rep.get("readout"), "prompt": (rep.get("prompt") or "")[:60]})


def fixture():
    thr = load_run_cfg()["g4_steering_effect_min_abs"]
    curve = {-0.6: 0.10, -0.2: 0.30, 0.0: 0.50, 0.2: 0.70, 0.6: 0.92}
    e = effect(curve)
    ok = abs(e) >= thr and coherent(curve)
    # a 0.003 dip in a 0.116 effect is tolerated; a 0.05 reversal in the same effect is not
    dip = {-0.6: 0.057, -0.4: 0.092, -0.2: 0.121, 0.0: 0.131, 0.2: 0.128, 0.4: 0.135, 0.6: 0.172}
    rev = {**dip, 0.2: 0.081}
    tol_ok = coherent(dip) and not coherent(rev)
    pl_ok = _fixture_placebo()
    return GateResult(NAME + "[fixture]", ok and tol_ok and all(pl_ok.values()), {"effect": round(e, 3), "threshold": thr, "monotone": coherent(curve),
                                                          "small_dip_tolerated": coherent(dip), "reversal_caught": not coherent(rev),
                                                          "placebo_rule_2026_10_03_1": pl_ok, "rules": GATE_RULES_VERSION})


def _fixture_placebo():
    """2026-10-03.1: a 48-token effect over 16 flat placebos PASSes; the same target with one placebo moving more FAILs
    (criterion 3); a placebo-sized target FAILs (criterion 1)."""
    import math
    from probe import steer_exact as SE
    lams = [-0.4, -0.2, 0.0, 0.2, 0.4]
    cells = [f"{o}/{u}" for o in ("safe_first", "risky_first") for u in ("tokens", "points", "dollars")]
    its = [{"n": n, "cell": c} for n in range(10, 181, 5) for c in cells]
    P = lambda k, l: [1 / (1 + math.exp(-((it["n"] - 88 + k * l) / 8))) for it in its]
    def by(tk, pks):
        d = {"v": {l: P(tk, l) for l in lams}}
        d.update({f"p{j}": {l: P(k, l) for l in lams} for j, k in enumerate(pks)})
        return d
    coh = {l: True for l in lams}
    pl = [f"p{j}" for j in range(16)]
    return {"pass": SE.g4_verdict(its, by(60, [0] * 16), "v", pl, lams, coh, n_boot=100)["verdict"] == "PASS",
            "big_placebo_fails": SE.g4_verdict(its, by(60, [0] * 15 + [70]), "v", pl, lams, coh, n_boot=50)["verdict"] == "FAIL",
            "placebo_sized_fails": SE.g4_verdict(its, by(10, [8] * 16), "v", pl, lams, coh, n_boot=50)["verdict"] == "FAIL"}
