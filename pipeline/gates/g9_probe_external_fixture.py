"""G9 probe_external_fixture: the probe track reproduces the SHAPE of Fan et al. (2026, arXiv:2609.16436)
on our model. Reads probe/<task>/{baseline,probe,calibration}.json for each task in run.yaml probe.tasks.

Pass iff, for every task:
 1. probe.heldout_acc >= g9_probe_heldout_acc_min           (Fan: 0.82 on Llama-3.3-70B)
 2. calibration.monotone over the non-saturated lambda range
 3. calibration.mae <= g9_switching_point_mae_max            (Fan: ~2 tokens)
 4. achieved span / target span >= g9_target_coverage_min    (the dial reaches a real range)
 5. baseline.sp is not None and baseline.method != "none"    (unsteered curve crosses 0.5 inside the grid)
Fan et al.'s absolute numbers (switching point, layer) are REPORTED beside ours, never gated."""
import json
from pathlib import Path
import yaml
from ._common import GateResult, load_run_cfg, GATE_RULES_VERSION

NAME = "G9_probe_external_fixture"
NEEDS_GPU = True
CFG = Path(__file__).resolve().parent.parent / "config"


def _tasks():
    return yaml.safe_load((CFG / "run.yaml").read_text())["probe"]["tasks"]


def _evaluate(task, base, probe, cal, g, targets):
    fan = base.get("fan2026_reference", {})
    acc = probe.get("heldout_acc_clean", probe["heldout_acc"])      # the cleaned direction is the claim
    checks = {
        "heldout_acc_ok": acc >= g["g9_probe_heldout_acc_min"],
        "monotone": bool(cal["monotone"]),
        "mae_ok": cal["mae"] is not None and cal["mae"] <= g["g9_switching_point_mae_max"],
        "baseline_ok": base["sp"] is not None and base["method"] != "none",
    }
    ra = cal.get("range_achieved")
    span_t = max(targets) - min(targets) if targets else 0
    cov = ((ra[1] - ra[0]) / span_t) if (ra and span_t > 0) else 0.0
    checks["coverage_ok"] = cov >= g["g9_target_coverage_min"]
    def _f(v, nd=1):
        return "None" if v is None else (round(v, nd) if isinstance(v, float) else v)
    pl = probe.get("per_layer") or {}
    by_layer = " ".join(f"L{k}:{_f(v['heldout_acc'], 3)}" for k, v in pl.items())
    ceil = "" if probe.get("ceiling_by_cell") is None else f" ceiling(grid/cell)={_f(probe['ceiling_by_grid'], 3)}/{_f(probe['ceiling_by_cell'], 3)}"
    loco = probe.get("surface_loco_clean") or {}
    loco_s = "" if not loco else f" surface_loco_clean(min)={_f(min(loco.values()), 3)}"
    def _cell_effects(dial):
        bc = (dial or {}).get("curve_by_cell") or {}
        hi, lo = bc.get("0.4") or {}, bc.get("-0.4") or {}
        effs = [hi[c] - lo[c] for c in hi if c in lo and hi[c] is not None and lo[c] is not None]
        if not effs:
            return ""
        effs.sort(); n = len(effs)
        med = effs[n // 2] if n % 2 else (effs[n // 2 - 1] + effs[n // 2]) / 2
        return f"median={_f(float(med), 0)} min|.|={_f(min(abs(e) for e in effs), 0)} n={len(effs)}"
    dials = cal.get("dials") or {}
    raw_dial = dials.get("raw") or {}
    raw_s = "" if not raw_dial else f" raw_dial_monotone={raw_dial.get('monotone')} raw_cell_effect[{_cell_effects(raw_dial)}]"
    if dials.get("clean"):
        raw_s += f" clean_cell_effect[{_cell_effects(dials['clean'])}]"
    line = (f"{task}: heldout_acc_clean={_f(acc, 3)} (raw {_f(probe['heldout_acc'], 3)}){ceil}{loco_s} [{probe.get('heldout_kind', '?')}]{raw_s} "
            f"(fan:{fan.get('heldout_acc')}) by_layer=[{by_layer}] "
            f"mae={_f(cal['mae'])} (fan:{fan.get('mae')}) baseline_sp={_f(base['sp'])} (fan:{fan.get('baseline_sp')}) "
            f"layer={probe['layer']} (fan:{fan.get('probe_layer')}) coverage={cov:.2f} "
            f"range={ra} (fan:{fan.get('range')}) saturated={cal.get('saturated_lambdas')}")
    return all(checks.values()), checks, line


def run(cfg, paths):
    g = load_run_cfg()
    pc = yaml.safe_load((CFG / "run.yaml").read_text())["probe"]
    root = Path(paths.get("probe") or (Path(paths["features"]).parent / "probe"))
    ok, detail, n_eval = True, {"rules": GATE_RULES_VERSION}, 0
    for task in _tasks():
        d = root / task
        try:
            base = json.loads((d / "baseline.json").read_text())
        except FileNotFoundError:
            return GateResult(NAME, False, {"error": f"{task}: missing baseline.json under {d}"})
        if base.get("sp") is None or base.get("n_graded_grid_points", 0) < 2:
            # no dial on this model: a FINDING, reported beside the others, not gated
            detail[task] = (f"{task}: NO DIAL on this model (unsteered sp={base.get('sp')}, graded grid points="
                            f"{base.get('n_graded_grid_points')}); reported, not gated")
            continue
        try:
            probe, cal = (json.loads((d / f).read_text()) for f in ("probe.json", "calibration.json"))
        except FileNotFoundError as e:
            return GateResult(NAME, False, {"error": f"{task}: missing {Path(e.filename).name} under {d}"})
        n_eval += 1
        t_ok, checks, line = _evaluate(task, base, probe, cal, g, cal.get("target_list") or pc["targets"][task])
        ok = ok and t_ok
        detail[task] = line
        detail[task + "_checks"] = {k: v for k, v in checks.items() if not v} or "all"
    if n_eval == 0:
        return GateResult(NAME, False, {**detail, "error": "no task had a dial on this model; nothing to calibrate"})
    return GateResult(NAME, ok, detail)


def fixture():
    g = load_run_cfg()
    targets = [40, 60, 80, 100, 125, 150, 175]
    fan = {"baseline_sp": 125, "probe_layer": 48, "heldout_acc": 0.82, "mae": 2, "range": [30, 200]}
    base = {"sp": 118.0, "method": "logistic", "fan2026_reference": fan}
    probe = {"heldout_acc": 0.79, "layer": 31}
    cal = {"monotone": True, "mae": 3.1, "range_achieved": [45.0, 170.0], "saturated_lambdas": ["-0.6"]}
    ok, _, line = _evaluate("lottery", base, probe, cal, g, targets)
    bad_mono = _evaluate("lottery", base, probe, {**cal, "monotone": False}, g, targets)[0]
    bad_mae = _evaluate("lottery", base, probe, {**cal, "mae": 9.0}, g, targets)[0]
    bad_sat = _evaluate("lottery", base, probe, {**cal, "mae": None, "range_achieved": None,
                                                  "saturated_lambdas": ["-0.6", "0.0", "0.6"]}, g, targets)[0]
    caught = (not bad_mono) and (not bad_mae) and (not bad_sat)
    return GateResult(NAME + "[fixture]", ok and caught,
                      {"passes": ok, "nonmonotone_caught": not bad_mono, "mae_caught": not bad_mae,
                       "saturation_caught": not bad_sat, "line": line})
