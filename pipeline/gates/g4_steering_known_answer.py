"""G4 steering_known_answer: if we can't move an easy behavior by steering a known vector, we can't
interpret a null on a hard one. Reproduce a published effect (a persona vector shifting an obvious
readout) and require a monotone-ish dose-response with |effect| over the sweep >= threshold. Real run
reads features/steering_report.json (produced by analyze.steer on a calibration prompt); fixture
proves the effect-size + monotonicity logic."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg

NAME = "G4_steering_known_answer"
NEEDS_GPU = True


def effect(curve):
    """curve: {strength: readout}. Effect = readout(max+) - readout(max-)."""
    ks = sorted(curve, key=float)
    return curve[ks[-1]] - curve[ks[0]]


def coherent(curve):
    """coherence must not collapse before the effect appears: monotone in the tested range."""
    ks = sorted(curve, key=float)
    vals = [curve[k] for k in ks]
    incr = all(y >= x - 1e-6 for x, y in zip(vals, vals[1:]))
    decr = all(y <= x + 1e-6 for x, y in zip(vals, vals[1:]))
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
    return GateResult(NAME, ok, {"effect": round(e, 3), "threshold": thr, "monotone": coherent(curve)})


def fixture():
    thr = load_run_cfg()["g4_steering_effect_min_abs"]
    curve = {-0.6: 0.10, -0.2: 0.30, 0.0: 0.50, 0.2: 0.70, 0.6: 0.92}
    e = effect(curve)
    ok = abs(e) >= thr and coherent(curve)
    return GateResult(NAME + "[fixture]", ok, {"effect": round(e, 3), "threshold": thr, "monotone": coherent(curve)})
