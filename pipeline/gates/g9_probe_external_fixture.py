"""G9 probe_external_fixture: the probe track reproduces the SHAPE of Fan et al. (2026, arXiv:2609.16436)
on our model. Reads probe/<task>/{baseline,probe,calibration}.json for each task in run.yaml probe.tasks.

Rules 2026-09-17.2. Pass iff, for every task with a dial:
 1. probe.heldout_acc_clean >= g9_probe_heldout_acc_min      (the CLEANED direction; Fan: 0.82 on Llama-3.3-70B)
 2. baseline.sp is not None and baseline.method != "none"    (unsteered curve crosses 0.5 inside the grid)
 3. the cleaned dial's PER-SURFACE-CELL effect sp(+lam) - sp(-lam) (probe.cell_effects):
    a. sign agreement in >= g9_cell_sign_agree_min of the cells,
    b. median per-cell |effect| >= g9_cell_effect_min tokens,
    c. every cell's 95% bootstrap interval excludes zero.
NOT_EVALUABLE (a third state, neither pass nor fail, still blocks spend) when any cell has fewer than
g9_cell_min_n trials per grid point, or no stored interval: an interval from two trials per point carries
no information, and a gate that returned pass or fail on it would be the quiet version of the thing it
exists to prevent.
Descriptive, reported on the line and never gated: the pooled lambda -> sp curve (monotone, MAE, coverage;
a mixture across cells), the raw dial's per-cell effects, per-layer held-out accuracy, the label-noise
ceilings, surface leave-one-cell-out, and Fan et al.'s absolute numbers."""
import json
from pathlib import Path
import yaml
from ._common import GateResult, load_run_cfg, GATE_RULES_VERSION, NOT_EVALUABLE
from probe.cell_effects import cell_effects, fmt

NAME = "G9_probe_external_fixture"
NEEDS_GPU = True
CFG = Path(__file__).resolve().parent.parent / "config"


def _tasks():
    return yaml.safe_load((CFG / "run.yaml").read_text())["probe"]["tasks"]


def _f(v, nd=1):
    return "None" if v is None else (round(v, nd) if isinstance(v, float) else v)


def _evaluate(task, base, probe, cal, g, targets):
    """-> (status, checks, line). status in {"pass", "fail", "not_evaluable"}."""
    fan = base.get("fan2026_reference", {})
    acc = probe.get("heldout_acc_clean", probe["heldout_acc"])      # the cleaned direction is the claim
    dials = cal.get("dials") or {}
    clean, raw = dials.get("clean") or {}, dials.get("raw") or {}
    kw = dict(lam_pref=g.get("g9_cell_effect_lambda", 0.4), min_n=g.get("g9_cell_min_n", 6),
              min_cells=g.get("g9_cell_min_cells", 6))
    ce = cell_effects(clean, **kw)
    checks = {
        "heldout_acc_ok": acc >= g["g9_probe_heldout_acc_min"],
        "baseline_ok": base["sp"] is not None and base["method"] != "none",
        "cell_sign_agree": ce["evaluable"] and ce["sign_agree"] >= g["g9_cell_sign_agree_min"],
        "cell_effect_size": ce["evaluable"] and ce["median_abs"] >= g["g9_cell_effect_min"],
        "cell_ci_exclude_zero": ce["evaluable"] and ce["all_exclude_zero"],
    }
    # descriptive: pooled curve
    ra = cal.get("range_achieved")
    span_t = max(targets) - min(targets) if targets else 0
    cov = ((ra[1] - ra[0]) / span_t) if (ra and span_t > 0) else 0.0
    pl = probe.get("per_layer") or {}
    by_layer = " ".join(f"L{k}:{_f(v['heldout_acc'], 3)}" for k, v in pl.items())
    ceil = "" if probe.get("ceiling_by_cell") is None else f" ceiling(grid/cell)={_f(probe['ceiling_by_grid'], 3)}/{_f(probe['ceiling_by_cell'], 3)}"
    loco = probe.get("surface_loco_clean") or {}
    loco_s = "" if not loco else f" surface_loco_clean(min)={_f(min(loco.values()), 3)}"
    raw_s = "" if not raw else f" RAW(descriptive) {fmt(cell_effects(raw, **kw))}"
    line = (f"{task}: heldout_acc_clean={_f(acc, 3)} (raw {_f(probe['heldout_acc'], 3)}){ceil}{loco_s} [{probe.get('heldout_kind', '?')}] "
            f"(fan:{fan.get('heldout_acc')}) by_layer=[{by_layer}] layer={probe['layer']} (fan:{fan.get('probe_layer')}) "
            f"baseline_sp={_f(base['sp'])} (fan:{fan.get('baseline_sp')}) | CLEAN(gated) {fmt(ce)}{raw_s} | "
            f"pooled(descriptive) monotone={cal.get('monotone')} mae={_f(cal.get('mae'))} (fan:{fan.get('mae')}) "
            f"coverage={cov:.2f} range={ra} (fan:{fan.get('range')}) saturated={cal.get('saturated_lambdas')}")
    if not ce["evaluable"] and checks["heldout_acc_ok"] and checks["baseline_ok"]:
        return NOT_EVALUABLE, checks, line
    return ("pass" if all(checks.values()) else "fail"), checks, line


def run(cfg, paths):
    g = load_run_cfg()
    pc = yaml.safe_load((CFG / "run.yaml").read_text())["probe"]
    root = Path(paths.get("probe") or (Path(paths["features"]).parent / "probe"))
    statuses, detail, n_eval = [], {"rules": GATE_RULES_VERSION}, 0
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
        status, checks, line = _evaluate(task, base, probe, cal, g, cal.get("target_list") or pc["targets"][task])
        statuses.append(status)
        detail[task] = line
        detail[task + "_status"] = status
        detail[task + "_checks"] = {k: v for k, v in checks.items() if not v} or "all"
    if n_eval == 0:
        return GateResult(NAME, False, {**detail, "error": "no task had a dial on this model; nothing to calibrate"})
    status = "fail" if "fail" in statuses else (NOT_EVALUABLE if NOT_EVALUABLE in statuses else "pass")
    return GateResult(NAME, status == "pass", detail, status=status)


def _dial(effects, se=3.0, n=6, lam=0.4):
    """Synthetic dial block: six cells with sp(+lam) = 50 + eff/2, sp(-lam) = 50 - eff/2."""
    cells = ["risky_first/dollars", "risky_first/points", "risky_first/tokens",
             "safe_first/dollars", "safe_first/points", "safe_first/tokens"]
    hi = {c: 50 + e / 2 for c, e in zip(cells, effects)}
    lo = {c: 50 - e / 2 for c, e in zip(cells, effects)}
    det = lambda sps: {c: {"sp": v, "se": se, "n_per_point": n} for c, v in sps.items()}
    return {"curve_by_cell": {str(lam): hi, str(-lam): lo, "0.0": {c: 50.0 for c in cells}},
            "cell_detail_by_lambda": {str(lam): det(hi), str(-lam): det(lo)}, "sweep_agents": 6 * n,
            "monotone": True}


def fixture():
    g = load_run_cfg()
    targets = [30, 40, 50, 60, 80, 100]
    fan = {"baseline_sp": 125, "probe_layer": 48, "heldout_acc": 0.82, "mae": 2, "range": [30, 200]}
    base = {"sp": 53.7, "method": "logistic_lapse", "fan2026_reference": fan}
    probe = {"heldout_acc": 0.986, "heldout_acc_clean": 0.80, "layer": 31}
    good = [-22, -18, -25, -20, -30, -16]
    cal = {"monotone": True, "mae": 3.1, "range_achieved": [30.0, 80.0], "saturated_lambdas": [],
           "dials": {"clean": _dial(good), "raw": _dial([-11, -39, -53, -4, -43, 3])}}
    ev = lambda **kw: _evaluate("lottery", kw.get("base", base), kw.get("probe", probe), kw.get("cal", cal), g, targets)[0]
    st_pass, _, line = _evaluate("lottery", base, probe, cal, g, targets)
    caught = {
        "sign_disagreement_caught": ev(cal={**cal, "dials": {"clean": _dial([-22, 18, -25, 20, -30, -16])}}) == "fail",
        "small_effect_caught": ev(cal={**cal, "dials": {"clean": _dial([-6, -2, 2, -4, -8, -5])}}) == "fail",
        "ci_includes_zero_caught": ev(cal={**cal, "dials": {"clean": _dial(good, se=15.0)}}) == "fail",
        "heldout_caught": ev(probe={**probe, "heldout_acc_clean": 0.5}) == "fail",
        "underpowered_is_not_evaluable": ev(cal={**cal, "dials": {"clean": _dial(good, n=2)}}) == NOT_EVALUABLE,
        "no_interval_is_not_evaluable": ev(cal={**cal, "dials": {"clean": {"curve_by_cell": _dial(good)["curve_by_cell"], "sweep_agents": 12}}}) == NOT_EVALUABLE,
        "pooled_curve_not_gated": ev(cal={**cal, "monotone": False, "mae": 9.0, "range_achieved": None}) == "pass",
    }
    ok = st_pass == "pass" and all(caught.values())
    return GateResult(NAME + "[fixture]", ok, {"passes": st_pass == "pass", **caught, "line": line})
