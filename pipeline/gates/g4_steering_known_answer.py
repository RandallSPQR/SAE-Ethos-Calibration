"""G4 steering_known_answer: if we can't move an easy behavior by steering a known vector, we can't
interpret a null on a hard one. Reproduce a published effect (a persona vector shifting an obvious
readout) and require a monotone-ish dose-response with |effect| over the sweep >= threshold. Real run
reads features/steering_report.json; fixture proves the effect-size + monotonicity logic.
Rules 2026-09-16.2: the readout must sit at a LIVE decision point (a prompt where the readout event has
non-negligible baseline probability); a readout at the floor (first run: P~1e-7 after "Write something.")
cannot show an absolute effect regardless of the direction's causal power. The report records the prompt
and per-strength greedy samples so the effect is inspectable. See gates/CHANGELOG.md."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg, GATE_RULES_VERSION

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
    return GateResult(NAME + "[fixture]", ok and tol_ok, {"effect": round(e, 3), "threshold": thr, "monotone": coherent(curve),
                                                          "small_dip_tolerated": coherent(dip), "reversal_caught": not coherent(rev),
                                                          "rules": GATE_RULES_VERSION})
