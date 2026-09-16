"""G5 oracle_calibration: the verbalizer (watcher) is only useful if it's right on known inputs and
honest on empty ones. Require accuracy on labeled activations >= min AND confabulation rate on
zeroed/shuffled activations <= max. The confab rate sets how much weight the oracle's testimony gets
in the writeup (card §4.5.1: AVs may confabulate). Real run reads features/oracle_calibration.json;
fixture proves both thresholds."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg

NAME = "G5_oracle_calibration"
NEEDS_GPU = True


def _check(acc, confab, g):
    return acc >= g["g5_oracle_acc_min"] and confab <= g["g5_confab_rate_max"]


def run(cfg, paths):
    g = load_run_cfg()
    p = Path(paths["features"]) / "oracle_calibration.json"
    if not p.exists():
        return GateResult(NAME, False, {"error": "features/oracle_calibration.json missing"})
    r = json.loads(p.read_text())
    ok = _check(r["accuracy"], r["confab_rate"], g)
    return GateResult(NAME, ok, {"accuracy": round(r["accuracy"], 3), "confab_rate": round(r["confab_rate"], 3),
                                 "min_acc": g["g5_oracle_acc_min"], "max_confab": g["g5_confab_rate_max"]})


def fixture():
    g = load_run_cfg()
    ok = _check(0.72, 0.18, g) and not _check(0.72, 0.50, g)
    return GateResult(NAME + "[fixture]", ok, {"good": "acc=0.72,confab=0.18", "bad": "confab=0.50"})
