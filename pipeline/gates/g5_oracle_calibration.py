"""G5 oracle_calibration (rules 2026-09-16.2): the oracle was trained to describe and has no abstain
mode (first run: 11/12 correct on real activations, 100% confident-specific on zeroed/shuffled ones). So
the gate measures DISCRIMINATION, not abstention: for each real activation a matched null (shuffled) is
verbalized too, and the pair counts as discriminated iff the real description agrees with the known label
of its text and the null description does not. Require paired discrimination >= g5_paired_discrimination_min
and accuracy >= g5_oracle_acc_min. The confabulation rate is REPORTED and sets the oracle's weight in
the writeup ("paired only" at 100%): it is a hypothesis generator checked against the SAE, never
standalone evidence. Real run reads features/oracle_calibration.json; fixture proves the thresholds."""
import json
from pathlib import Path
from ._common import GateResult, load_run_cfg, GATE_RULES_VERSION

NAME = "G5_oracle_calibration"
NEEDS_GPU = True


def _check(acc, paired, g):
    return acc >= g["g5_oracle_acc_min"] and paired >= g.get("g5_paired_discrimination_min", 0.6)


def paired_discrimination(pairs):
    """pairs: [{'real_hit': bool, 'null_hit': bool}] -> fraction where real hits its label and null does not."""
    if not pairs:
        return 0.0
    return sum(1 for p in pairs if p["real_hit"] and not p["null_hit"]) / len(pairs)


def run(cfg, paths):
    g = load_run_cfg()
    p = Path(paths["features"]) / "oracle_calibration.json"
    if not p.exists():
        return GateResult(NAME, False, {"error": "features/oracle_calibration.json missing"})
    r = json.loads(p.read_text())
    if "pairs" not in r:
        return GateResult(NAME, False, {"error": "report has no paired real/null verbalizations (rules 2026-09-16.2)",
                                        "rules": GATE_RULES_VERSION})
    paired = paired_discrimination(r["pairs"])
    ok = _check(r["accuracy"], paired, g)
    weight = "paired only" if r.get("confab_rate", 0) > g["g5_confab_rate_max"] else "standalone with caveat"
    return GateResult(NAME, ok, {"rules": GATE_RULES_VERSION, "accuracy": round(r["accuracy"], 3),
                                 "paired_discrimination": round(paired, 3), "confab_rate": round(r.get("confab_rate", 0), 3),
                                 "oracle_weight": weight, "min_acc": g["g5_oracle_acc_min"],
                                 "min_paired": g.get("g5_paired_discrimination_min", 0.6), "n_pairs": len(r["pairs"])})


def fixture():
    g = load_run_cfg()
    good = [{"real_hit": True, "null_hit": False}] * 9 + [{"real_hit": True, "null_hit": True}]
    bad = [{"real_hit": True, "null_hit": True}] * 10          # accurate but cannot tell real from null
    pd_good, pd_bad = paired_discrimination(good), paired_discrimination(bad)
    ok = _check(0.9, pd_good, g) and not _check(0.9, pd_bad, g)
    return GateResult(NAME + "[fixture]", ok, {"good_paired": pd_good, "bad_paired": pd_bad,
                                               "accurate_but_undiscriminating_blocked": not _check(0.9, pd_bad, g),
                                               "rules": GATE_RULES_VERSION})
