"""G1 replay_fidelity: did teacher-forced replay reproduce the computation the generation model ran?

Operates on CONTINUATION-RELATIVE, EQUAL-LENGTH arrays that replay produces (see transcript.schema.md):
  generated_ids, replay_predicted_ids, generation_logprob, replay_logprob.
No slicing of whole-sequence arrays by assistant_span — that mixed coordinate systems (the old bug).

Two modes:
  exact   (T=0 calibration): replay_predicted_ids == generated_ids at ~100%. A real checksum.
  logprob (T>0 behavioral): the SAME sampled token must get ~the same conditional logprob under replay
          as it did at generation. Criterion = max |generation_logprob - replay_logprob| over the span
          is tiny (numerical tolerance). This is a genuine stochastic-run checksum, NOT a
          "median token prob >= 0.5" plausibility threshold (which was wrong and is removed).

Mode is selected by run.yaml sampling.temperature (0 -> exact). Missing raw ids -> hard fail."""
from ._common import GateResult, load_run_cfg, iter_merged

try:
    import yaml
    from pathlib import Path
    _T = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "run.yaml").read_text())["sampling"]["temperature"]
except Exception:
    _T = 0.8

NAME = "G1_replay_fidelity"
NEEDS_GPU = True
MODE = "exact" if _T == 0 else "logprob"


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
    if MODE == "exact":
        thr = gc.get("g1_exact_match_min", 0.999)
        rates = [_exact_rate(x) for x in rows]
        n_valid = sum(1 for r in rates if r is not None)
        if n_valid != n_gen:
            return GateResult(NAME, False, {"mode": "exact",
                                            "error": f"aligned id arrays for {n_valid}/{n_gen} only"})
        mean = sum(rates) / n_valid
        return GateResult(NAME, mean >= thr, {"mode": "exact", "exact_match": round(mean, 4),
                                              "threshold": thr, "n": n_valid})
    tol = gc.get("g1_logprob_tol", 0.05)
    gaps = [_max_logprob_gap(r) for r in rows]
    n_valid = sum(1 for g in gaps if g is not None)
    if n_valid != n_gen:
        return GateResult(NAME, False, {"mode": "logprob",
                                        "error": f"logprob arrays for {n_valid}/{n_gen} only"})
    worst = max(gaps)
    return GateResult(NAME, worst <= tol, {"mode": "logprob", "worst_logprob_gap": round(worst, 5),
                                          "tol": tol, "n": n_valid})


def fixture():
    gc = load_run_cfg()
    ids = list(range(20))
    good = {"tokens": {"generated_ids": ids, "replay_predicted_ids": ids,
                       "generation_logprob": [-0.3] * 20, "replay_logprob": [-0.3001] * 20}}
    bad_exact = {"tokens": {"generated_ids": ids, "replay_predicted_ids": [99] * 20,
                            "generation_logprob": [-0.3] * 20, "replay_logprob": [-2.0] * 20}}
    exact_ok = _exact_rate(good) >= gc.get("g1_exact_match_min", 0.999) and _exact_rate(bad_exact) < 0.999
    lp_ok = _max_logprob_gap(good) <= gc.get("g1_logprob_tol", 0.05) and \
            _max_logprob_gap(bad_exact) > gc.get("g1_logprob_tol", 0.05)
    return GateResult(NAME + "[fixture]", exact_ok and lp_ok,
                      {"exact_logic_ok": exact_ok, "logprob_logic_ok": lp_ok, "active_mode": MODE})
