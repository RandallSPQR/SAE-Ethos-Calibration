"""G1 replay_fidelity: did teacher-forced replay reproduce the computation the generation model ran?

Operates on CONTINUATION-RELATIVE, EQUAL-LENGTH arrays that replay produces (see transcript.schema.md):
  generated_ids, replay_predicted_ids, generation_logprob, replay_logprob.
No slicing of whole-sequence arrays by assistant_span — that mixed coordinate systems (the old bug).

Two modes, selected PER TRANSCRIPT from the temperature the transcript records it was generated at
(row["sampling"]["temperature"]); a config value can never point the gate at the wrong criterion, and a
transcript without the field is a hard fail (rules 2026-09-16.2).

  exact   (T=0): replay_predicted_ids == generated_ids, with a CONDITIONAL excuse for kernel-order
          noise: a flip at position k is excused only if generation's own top-2 margin at k was below
          g1_flip_margin_excuse (nats) AND the generated token is within replay's top-2. A flip where the
          margin was wide is a hard fail (that is what a template mismatch looks like; it also clusters
          at the turn boundary). Unexcused flip rate must be <= 1 - g1_exact_match_min.
  logprob (T>0): the SAME sampled token must get ~the same conditional logprob under replay as it did at
          generation: max |generation_logprob - replay_logprob| over the span <= g1_logprob_tol.

Missing raw ids -> hard fail.

  mixed   (rules 2026-09-29.2, the 27B protocol: served bf16, replayed fp32): selected when the rows record a
          served dtype (row["sampling"]["served_dtype"]) other than the replay dtype. The two sides no longer
          compute the same numbers, so a fixed 0.05-nat max cannot separate dtype noise from a defect. The
          logprob criterion is instead derived by `derive_mixed` from a committed calibration file (a separate,
          never-analysed run on the same stack; `gates/g1_calibrate.py` builds it) whose sha256 is pinned in
          run.yaml: per-row worst gap w_i and mean gap m_i; FAIL if more than row_exceed_max of rows have
          w_i > row_factor x q_row_quantile(w_cal), or if median(m_i) > bulk_factor x median(m_cal). The
          calibration is refused (G1 FAIL) unless it has >= min_rows rows, its served-vs-fp32 noise is within
          crosscheck_ratio_max of pure HF dtype noise on the same rows, and every planted defect it carries
          reads FAIL under the thresholds derived from it. Rows without a recorded served dtype take the
          fixed-tolerance path, so a bf16-served run that forgot to record its dtype fails rather than passes."""
import hashlib
import json
import statistics as st
from pathlib import Path

from ._common import GateResult, load_run_cfg, iter_merged, GATE_RULES_VERSION

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_PLANTS = ("off_by_one", "boundary_shift", "sparse_1pct", "template_prefix")

NAME = "G1_replay_fidelity"
NEEDS_GPU = True


def row_mode(row):
    t = (row.get("sampling") or {}).get("temperature")
    if t is None:
        return None
    return "exact" if float(t) == 0.0 else "logprob"


def _arrays(row):
    t = row.get("tokens") or {}
    g = t.get("generated_ids")
    p = t.get("replay_predicted_ids")
    gl = t.get("generation_logprob")
    rl = t.get("replay_logprob")
    return g, p, gl, rl


def _exact_rate(row):
    g, p, _, _ = _arrays(row)
    if not (g and p) or len(g) != len(p):
        return None
    return sum(a == b for a, b in zip(g, p)) / len(g)


def flips(row, excuse_margin):
    """(n_flips, n_excused, unexcused_positions). A flip is excused iff the generation-time top-2 margin
    at that position was < excuse_margin AND the generated token is in replay's top-2 at that position.
    Missing margin/top-2 metadata means NO flip can be excused."""
    g, p, _, _ = _arrays(row)
    t = row.get("tokens") or {}
    margins = t.get("sampled_top2_margin")
    top2 = t.get("replay_top2_ids")
    n_flips, n_exc, unexcused = 0, 0, []
    for k, (a, b) in enumerate(zip(g, p)):
        if a == b:
            continue
        n_flips += 1
        m_ok = margins is not None and k < len(margins) and margins[k] is not None and margins[k] < excuse_margin
        t_ok = top2 is not None and k < len(top2) and a in (top2[k] or [])
        if m_ok and t_ok:
            n_exc += 1
        else:
            unexcused.append(k)
    return n_flips, n_exc, unexcused


def _max_logprob_gap(row):
    g, _, gl, rl = _arrays(row)
    if not (gl and rl) or len(gl) != len(rl):
        return None
    return max(abs(a - b) for a, b in zip(gl, rl))


def row_served_dtype(row):
    return (row.get("sampling") or {}).get("served_dtype")


def row_stats(gl, rl):
    """(w, m, k): worst |generation - replay| logprob gap over the span, mean gap, position of the worst."""
    d = [abs(a - b) for a, b in zip(gl, rl)]
    w = max(d)
    return w, sum(d) / len(d), d.index(w)


def quantile(xs, p):
    xs = sorted(xs)
    k = p * (len(xs) - 1)
    i = int(k)
    j = min(i + 1, len(xs) - 1)
    return xs[i] + (xs[j] - xs[i]) * (k - i)


def derive_mixed(cal, mc):
    """Thresholds and validity from a calibration dict (rules 2026-09-29.2). Pure function of the file's rows, so
    nobody can hand-edit a tolerance: G1 re-derives it every time from the committed per-row statistics."""
    w = [r["w"] for r in cal["rows"]]
    m = [r["m"] for r in cal["rows"]]
    thr = {"tol_row": round(mc["row_factor"] * quantile(w, mc["row_quantile"]), 6),
           "tol_bulk": round(mc["bulk_factor"] * st.median(m), 6)}
    val = {"n_rows": len(w), "rows_ok": len(w) >= mc["min_rows"]}
    xm = [r["m"] for r in (cal.get("crosscheck") or {}).get("rows", [])]
    ratio = (st.median(m) / st.median(xm)) if xm and st.median(xm) > 0 else None
    val["crosscheck_ratio"] = None if ratio is None else round(ratio, 4)
    val["crosscheck_ok"] = ratio is not None and ratio <= mc["crosscheck_ratio_max"]
    val["clean_passes"] = judge_mixed(w, m, thr, mc)["ok"]
    plants = cal.get("planted") or {}
    val["planted"] = {name: ("absent" if name not in plants else
                             ("FAIL" if not judge_mixed([r["w"] for r in plants[name]], [r["m"] for r in plants[name]],
                                                        thr, mc)["ok"] else "PASS (defect not caught)"))
                      for name in REQUIRED_PLANTS}
    val["valid"] = (val["rows_ok"] and val["crosscheck_ok"] and val["clean_passes"]
                    and all(v == "FAIL" for v in val["planted"].values()))
    return thr, val


def judge_mixed(w, m, thr, mc, ks=None, uids=None):
    over = [i for i, x in enumerate(w) if x > thr["tol_row"]]
    frac = len(over) / max(1, len(w))
    med_m = st.median(m) if m else float("inf")
    out = {"ok": bool(w) and frac <= mc["row_exceed_max"] and med_m <= thr["tol_bulk"],
           "frac_rows_over_tol": round(frac, 5), "median_row_mean_gap": round(med_m, 6)}
    if ks is not None and over:
        out["over_tol_at_boundary"] = sum(1 for i in over if ks[i] < mc["boundary_tokens"])   # reported: a template
        out["n_over_tol"] = len(over)                                                          # defect clusters here
    if uids is not None and over:
        out["over_tol_uids"] = [uids[i] for i in sorted(over, key=lambda i: -w[i])[:5]]
    return out


def load_calibration(mc):
    """(cal, error). The calibration is a committed file pinned by sha256; anything else is refused."""
    rel, want = mc.get("calibration"), mc.get("calibration_sha256")
    if not rel or not want:
        return None, "no committed calibration (gates.g1_mixed.calibration / calibration_sha256 unset)"
    f = (ROOT / rel) if not Path(rel).is_absolute() else Path(rel)
    if not f.exists():
        return None, f"calibration file {rel} missing"
    got = hashlib.sha256(f.read_bytes()).hexdigest()
    if got != want:
        return None, f"calibration sha256 {got[:16]} != pinned {want[:16]}"
    return json.loads(f.read_text()), None


def _mixed_logprob(lp_rows, served, want, gc):
    """The mixed-dtype logprob criterion. Returns (ok, detail)."""
    mc = gc["g1_mixed"]
    cal, err = load_calibration(mc)
    if err:
        return False, {"mode": "mixed", "error": err}
    ident = {"served_dtype": (cal.get("served_dtype"), served), "replay_dtype": (cal.get("replay_dtype"), want)}
    try:
        import yaml
        rev = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())["target_model"].get("revision")
        ident["model_revision"] = ((cal.get("model") or {}).get("revision"), rev)
    except Exception:
        ident["model_revision"] = ("unreadable", "unreadable")
    bad = {k: v for k, v in ident.items() if v[0] != v[1]}
    if bad:
        return False, {"mode": "mixed", "error": f"calibration identity differs from this run: {bad}"}
    shared = {r.get("run_id") for r in lp_rows} & {cal.get("run_id")}
    if shared - {None}:
        return False, {"mode": "mixed", "error": f"calibration run {sorted(shared)} is the judged run; the calibration is not the judged data"}
    thr, val = derive_mixed(cal, mc)
    if not val["valid"]:
        return False, {"mode": "mixed", "error": "calibration invalid", "calibration_validity": val}
    unaligned = [r["uid"] for r in lp_rows if (r.get("tokens") or {}).get("span_ids_equal_sampled") is not True]
    if unaligned:
        return False, {"mode": "mixed", "error": f"{len(unaligned)} rows lack span_ids_equal_sampled=True "
                                                 f"(raw-id replay is the precondition)", "examples": unaligned[:3]}
    st_rows = [row_stats(r["tokens"]["generation_logprob"], r["tokens"]["replay_logprob"]) for r in lp_rows]
    j = judge_mixed([x[0] for x in st_rows], [x[1] for x in st_rows], thr, mc,
                    ks=[x[2] for x in st_rows], uids=[r["uid"] for r in lp_rows])
    return j.pop("ok"), {"mode": "mixed", "served_dtype": served, **thr, "row_exceed_max": mc["row_exceed_max"],
                         "calibration_run": cal.get("run_id"), "calibration_rows": val["n_rows"],
                         "crosscheck_ratio": val["crosscheck_ratio"], **j,
                         "worst_logprob_gap": round(max(x[0] for x in st_rows), 5)}


def run(cfg, paths):
    gc = load_run_cfg()
    rows = list(iter_merged(paths["transcripts"], paths.get("replayed")))
    n_gen = len(rows)
    n_replayed = sum(1 for r in rows if r.get("_replayed"))
    # 100% cardinality: a checksum is meaningful only if it checks EVERY object that will be trusted.
    # Require n_gen == n_replayed == n_valid_arrays; never compute the statistic over a subset.
    if n_gen == 0:
        return GateResult(NAME, False, {"error": "no transcripts"})
    if n_replayed != n_gen:
        return GateResult(NAME, False, {"error": f"replay cardinality {n_replayed}/{n_gen}; every "
                                                 "continuation must be replayed before G1 runs"})
    # rules 2026-09-29.1: the replay's dtype is part of the checksum. A row that records replay_dtype must record the pinned
    # one (g1_replay_dtype, float32: fp32 both sides is the protocol); a replay in another dtype is a different instrument
    # and the gate says so instead of reporting a numerical gap as if it were fidelity. Old stores without the key are noted.
    want = str(gc.get("g1_replay_dtype", "float32"))
    seen = {str((r.get("tokens") or {}).get("replay_dtype")) for r in rows}
    seen.discard("None")
    if seen and seen != {want}:
        return GateResult(NAME, False, {"error": f"replay dtype {sorted(seen)} is not the pinned {want}; re-run the replay "
                                                 f"with T1_DTYPE={want}", "replay_dtype": sorted(seen), "rules": GATE_RULES_VERSION})
    # rules 2026-09-29.2: the served dtype, when recorded, selects the criterion (not from config). Mixed recording is refused.
    served_set = {row_served_dtype(r) for r in rows}
    if None in served_set and len(served_set) > 1:
        return GateResult(NAME, False, {"error": "served_dtype recorded on some rows only", "rules": GATE_RULES_VERSION})
    if len(served_set) > 1:
        return GateResult(NAME, False, {"error": f"several served dtypes in one store: {sorted(served_set)}",
                                        "rules": GATE_RULES_VERSION})
    served = next(iter(served_set))
    mixed = served is not None and str(served) != want
    modes = [row_mode(r) for r in rows]
    if any(m is None for m in modes):
        return GateResult(NAME, False, {"error": "transcript lacks sampling.temperature; the gate refuses to "
                                                 "pick a criterion from config", "rules": GATE_RULES_VERSION})
    exact_rows = [r for r, m in zip(rows, modes) if m == "exact"]
    lp_rows = [r for r, m in zip(rows, modes) if m == "logprob"]
    detail, ok = {"rules": GATE_RULES_VERSION, "n_exact_rows": len(exact_rows), "n_logprob_rows": len(lp_rows),
                  "replay_dtype": (sorted(seen)[0] if seen else "unrecorded (pre-2026-09-29 store)")}, True
    if exact_rows:
        thr = gc.get("g1_exact_match_min", 0.999)
        excuse = gc.get("g1_flip_margin_excuse", 0.25)
        rates = [_exact_rate(x) for x in exact_rows]
        if any(r is None for r in rates):
            return GateResult(NAME, False, {"mode": "exact", "error": "aligned id arrays missing on some rows"})
        n_tok = sum(len(x["tokens"]["generated_ids"]) for x in exact_rows)
        fl = [flips(x, excuse) for x in exact_rows]
        n_flips = sum(f[0] for f in fl); n_exc = sum(f[1] for f in fl)
        unexcused_rate = (n_flips - n_exc) / max(1, n_tok)
        ex_ok = (1 - unexcused_rate) >= thr
        ok = ok and ex_ok
        detail.update({"exact_match_raw": round(sum(rates) / len(rates), 4), "n_tokens": n_tok, "n_flips": n_flips,
                       "n_excused": n_exc, "unexcused_flip_rate": round(unexcused_rate, 5),
                       "excuse_margin_nats": excuse, "threshold": thr, "exact_ok": ex_ok})
        if not ex_ok:
            detail["unexcused_positions"] = [f[2][:5] for f in fl if f[2]][:3]
    if lp_rows and mixed:
        if any(_max_logprob_gap(r) is None for r in lp_rows):
            return GateResult(NAME, False, {"mode": "mixed", "error": "logprob arrays missing on some rows"})
        lp_ok, mdet = _mixed_logprob(lp_rows, str(served), want, gc)
        ok = ok and lp_ok
        detail.update({**mdet, "logprob_ok": lp_ok})
    elif lp_rows:
        tol = gc.get("g1_logprob_tol", 0.05)
        gaps = [_max_logprob_gap(r) for r in lp_rows]
        if any(g is None for g in gaps):
            return GateResult(NAME, False, {"mode": "logprob", "error": "logprob arrays missing on some rows"})
        worst = max(gaps)
        lp_ok = worst <= tol
        ok = ok and lp_ok
        detail.update({"worst_logprob_gap": round(worst, 5), "tol": tol, "logprob_ok": lp_ok})
    return GateResult(NAME, ok, detail)


def fixture():
    gc = load_run_cfg()
    excuse = gc.get("g1_flip_margin_excuse", 0.25)
    ids = list(range(20))
    good = {"tokens": {"generated_ids": ids, "replay_predicted_ids": ids,
                       "generation_logprob": [-0.3] * 20, "replay_logprob": [-0.3001] * 20}}
    bad_exact = {"tokens": {"generated_ids": ids, "replay_predicted_ids": [99] * 20,
                            "generation_logprob": [-0.3] * 20, "replay_logprob": [-2.0] * 20}}
    exact_ok = _exact_rate(good) >= gc.get("g1_exact_match_min", 0.999) and _exact_rate(bad_exact) < 0.999
    lp_ok = _max_logprob_gap(good) <= gc.get("g1_logprob_tol", 0.05) and \
            _max_logprob_gap(bad_exact) > gc.get("g1_logprob_tol", 0.05)
    # conditional excuse: one near-tie flip (margin 0.13, generated token in replay's top-2) is excused;
    # the same flip with a wide generation margin is NOT; a flip with no metadata is NOT.
    pred = list(ids); pred[9] = 99
    near = {"tokens": {"generated_ids": ids, "replay_predicted_ids": pred,
                       "sampled_top2_margin": [1.0] * 9 + [0.13] + [1.0] * 10,
                       "replay_top2_ids": [[i, 99] for i in ids]}}
    wide = {"tokens": {**near["tokens"], "sampled_top2_margin": [1.0] * 20}}
    nometa = {"tokens": {"generated_ids": ids, "replay_predicted_ids": pred}}
    f_near, f_wide, f_none = flips(near, excuse), flips(wide, excuse), flips(nometa, excuse)
    cond_ok = f_near == (1, 1, []) and f_wide[1] == 0 and f_wide[2] == [9] and f_none[1] == 0
    # mode comes from the transcript, never from config
    mode_ok = row_mode({"sampling": {"temperature": 0.0}}) == "exact" and \
        row_mode({"sampling": {"temperature": 0.8}}) == "logprob" and row_mode({}) is None
    # rules 2026-09-29.1: a replay in the wrong dtype is refused as such (the deep resample's bf16 replay)
    want = str(gc.get("g1_replay_dtype", "float32"))
    dtype_ok = ({str((r.get("tokens") or {}).get("replay_dtype")) for r in [{"tokens": {"replay_dtype": "bfloat16"}}]} != {want}) and \
               ({str((r.get("tokens") or {}).get("replay_dtype")) for r in [{"tokens": {"replay_dtype": want}}]} == {want})
    mixed_ok, mixed_detail = _mixed_fixture(gc)
    return GateResult(NAME + "[fixture]", exact_ok and lp_ok and cond_ok and mode_ok and dtype_ok and mixed_ok,
                      {"exact_logic_ok": exact_ok, "logprob_logic_ok": lp_ok, "conditional_excuse_ok": cond_ok,
                       "mode_from_transcript_ok": mode_ok, "dtype_pin_ok": dtype_ok, "mixed_dtype_ok": mixed_ok,
                       **mixed_detail, "rules": GATE_RULES_VERSION})


def _noise_rows(rng, n, L=120, sd=0.02):
    """Synthetic bf16-vs-fp32 pairs: generation logprobs, replay = generation + small diffuse noise."""
    out = []
    for _ in range(n):
        g = [-abs(rng.gauss(0.5, 0.7)) for _ in range(L)]
        out.append((g, [x + rng.gauss(0, sd) for x in g]))
    return out


def _plants(pairs):
    from .g1_calibrate import plant_all
    return plant_all(pairs)


def _mixed_fixture(gc):
    """rules 2026-09-29.2: derive on one synthetic run, judge another. Clean passes; each planted defect fails; an
    invalid calibration (too few rows, served noise far above HF dtype noise, a plant it cannot catch) is refused;
    the sha pin and the served-dtype selection work."""
    import random
    import tempfile
    from .g1_calibrate import calibration_record
    mc = gc["g1_mixed"]
    rng = random.Random(0)
    cal_pairs, run_pairs = _noise_rows(rng, 320), _noise_rows(rng, 400)
    xcheck = _noise_rows(rng, 320)                                # HF dtype noise, same scale
    tmpl = [(g, [r[0] - 1.5] + r[1:]) for g, r in _noise_rows(rng, 60)]   # a template defect: the first span token off
    cal = calibration_record(cal_pairs, xcheck, template_pairs=tmpl, run_id="cal_fixture",
                             served_dtype="bfloat16", replay_dtype="float32", model={"revision": "r"})
    thr, val = derive_mixed(cal, mc)
    stats = lambda ps: [row_stats(g, r) for g, r in ps]
    judge = lambda ps: judge_mixed([x[0] for x in stats(ps)], [x[1] for x in stats(ps)], thr, mc)["ok"]
    clean = judge(run_pairs)
    plants = _plants(run_pairs)
    plants_fail = all(not judge(p) for p in plants.values())
    few = derive_mixed({**cal, "rows": cal["rows"][:100]}, mc)[1]["valid"] is False
    loud = derive_mixed({**cal, "crosscheck": {"rows": [{"m": r["m"] / 5} for r in cal["crosscheck"]["rows"]]}}, mc)[1]
    loud_refused = loud["valid"] is False and loud["crosscheck_ok"] is False
    no_tmpl = derive_mixed({**cal, "planted": {k: v for k, v in cal["planted"].items() if k != "template_prefix"}}, mc)[1]
    absent_refused = no_tmpl["valid"] is False and no_tmpl["planted"]["template_prefix"] == "absent"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(cal, fh)
    good_sha = hashlib.sha256(Path(fh.name).read_bytes()).hexdigest()
    sha_ok = load_calibration({"calibration": fh.name, "calibration_sha256": good_sha})[1] is None and \
        load_calibration({"calibration": fh.name, "calibration_sha256": "0" * 64})[1] is not None and \
        load_calibration({"calibration": None, "calibration_sha256": None})[1] is not None
    Path(fh.name).unlink()
    sel_ok = row_served_dtype({"sampling": {"served_dtype": "bfloat16"}}) == "bfloat16" and row_served_dtype({}) is None
    ok = val["valid"] and clean and plants_fail and few and loud_refused and absent_refused and sha_ok and sel_ok
    return ok, {"mixed_detail": {"cal_valid": val["valid"], "clean_run_passes": clean, "plants_fail": plants_fail,
                                 "few_rows_refused": few, "loud_served_refused": loud_refused,
                                 "absent_plant_refused": absent_refused, "sha_pin_ok": sha_ok}}
