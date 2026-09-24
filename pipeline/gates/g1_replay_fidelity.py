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

Missing raw ids -> hard fail."""
from ._common import GateResult, load_run_cfg, iter_merged, GATE_RULES_VERSION

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
    modes = [row_mode(r) for r in rows]
    if any(m is None for m in modes):
        return GateResult(NAME, False, {"error": "transcript lacks sampling.temperature; the gate refuses to "
                                                 "pick a criterion from config", "rules": GATE_RULES_VERSION})
    exact_rows = [r for r, m in zip(rows, modes) if m == "exact"]
    lp_rows = [r for r, m in zip(rows, modes) if m == "logprob"]
    detail, ok = {"rules": GATE_RULES_VERSION, "n_exact_rows": len(exact_rows), "n_logprob_rows": len(lp_rows)}, True
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
    if lp_rows:
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
    return GateResult(NAME + "[fixture]", exact_ok and lp_ok and cond_ok and mode_ok,
                      {"exact_logic_ok": exact_ok, "logprob_logic_ok": lp_ok, "conditional_excuse_ok": cond_ok,
                       "mode_from_transcript_ok": mode_ok, "rules": GATE_RULES_VERSION})
