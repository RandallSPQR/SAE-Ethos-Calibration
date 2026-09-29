"""Build the G1 mixed-dtype calibration (rules 2026-09-29.2) and dry-run it on a store that has two replays.

The 27B is served bf16 (vLLM) and replayed fp32 (HF), so generation and replay no longer compute the same numbers
and G1's fixed 0.05-nat max cannot tell dtype noise from a defect. The tolerance is derived from a calibration run
on the same stack, not analysed, committed with its sha256 pinned in run.yaml before any run it judges:

  python -m gates.g1_calibrate build --transcripts RUN/generation --replay RUN/replay \\
         --crosscheck RUN/replay_hf_served_dtype --template-replay RUN/replay_template_defect --out FILE

  --replay           the protocol replay (fp32) of the calibration run: gives w_i, m_i per row
  --crosscheck       the same rows replayed by HF in the SERVED dtype: pure dtype noise, no vLLM; the ratio
                     median m(served vs fp32) / median m(HF served-dtype vs fp32) must be <= crosscheck_ratio_max,
                     or something beyond dtype (template, revision, kernels) differs between the two paths
  --template-replay  a subset replayed with a deliberately altered turn prefix: the realistic defect, must FAIL

Offline plants (off-by-one alignment, a boundary shift on the first span tokens, 1 % of rows misaligned) are
computed from the replay itself. G1 re-derives every threshold from the stored per-row statistics.

  python -m gates.g1_calibrate dryrun --replay A --crosscheck B --assume-served-dtype D --out FILE

splits a store by seed parity (calibrate on even, judge odd) to show what the rule does on real data."""
import argparse
import json
import re
from pathlib import Path

import yaml

from ._common import GATE_RULES_VERSION, load_run_cfg
from .g1_replay_fidelity import ROOT, derive_mixed, judge_mixed, row_stats


def _shift(g, r):
    return g[1:], r[:-1]


def plant_all(pairs):
    """Offline planted defects on (generation, replay) logprob pairs."""
    def boundary(g, r, k=3):
        r = list(r)
        r[:k] = r[1:k + 1]
        return g, r
    return {"off_by_one": [_shift(g, r) for g, r in pairs],
            "boundary_shift": [boundary(g, r) for g, r in pairs],
            "sparse_1pct": [_shift(g, r) if i % 100 == 0 else (g, r) for i, (g, r) in enumerate(pairs)]}


def _wm(pairs):
    return [dict(zip(("w", "m"), row_stats(g, r)[:2])) for g, r in pairs]


def calibration_record(pairs, xcheck_pairs, template_pairs=None, uids=None, **ident):
    rows = [{"uid": (uids[i] if uids else None), **dict(zip(("w", "m", "k"), row_stats(g, r)))}
            for i, (g, r) in enumerate(pairs)]
    planted = {k: _wm(v) for k, v in plant_all(pairs).items()}
    if template_pairs:
        planted["template_prefix"] = _wm(template_pairs)
    return {"rules": GATE_RULES_VERSION, **ident, "n_rows": len(rows), "rows": rows,
            "crosscheck": {"rows": [{"m": x["m"]} for x in _wm(xcheck_pairs)]}, "planted": planted}


def _load(d):
    out = {}
    for f in sorted(Path(d).rglob("*.jsonl")):
        for line in open(f):
            if line.strip():
                r = json.loads(line)
                out[r["uid"]] = r
    return out


def _pair(t):
    return t["generation_logprob"], t["replay_logprob"]


def _require_aligned(rows, name):
    bad = [u for u, r in rows.items() if r["tokens"].get("span_ids_equal_sampled") is not True]
    if bad:
        raise SystemExit(f"{name}: {len(bad)} rows lack span_ids_equal_sampled=True (e.g. {bad[:2]}); calibration refused")


def _model():
    import modelcfg
    tm = modelcfg.target()
    return {"hf_id": tm.get("hf_id"), "revision": tm.get("revision")}


def build(a):
    rep, xc = _load(a.replay), _load(a.crosscheck)
    gen = _load(a.transcripts)
    _require_aligned(rep, "replay")
    uids = sorted(set(rep) & set(xc))
    if len(uids) != len(rep):
        raise SystemExit(f"crosscheck covers {len(uids)}/{len(rep)} replay rows; every row must be cross-checked")
    served = {(gen[u].get("sampling") or {}).get("served_dtype") for u in uids}
    run_ids = {gen[u].get("run_id") for u in uids}
    rdt = {rep[u]["tokens"].get("replay_dtype") for u in uids}
    tf32 = {bool(rep[u]["tokens"].get("replay_tf32")) for u in uids}
    if len(tf32) != 1:
        raise SystemExit(f"replay_tf32 not uniform across the calibration replay: {tf32}")
    if len(served) != 1 or None in served or len(run_ids) != 1 or len(rdt) != 1:
        raise SystemExit(f"calibration identity not uniform: served {served}, run_id {run_ids}, replay_dtype {rdt}")
    tmpl = None
    if a.template_replay:
        tr = _load(a.template_replay)
        tmpl = [_pair(tr[u]["tokens"]) for u in sorted(tr)]
    cal = calibration_record([_pair(rep[u]["tokens"]) for u in uids],
                             [(rep[u]["tokens"]["replay_logprob"], xc[u]["tokens"]["replay_logprob"]) for u in uids],
                             template_pairs=tmpl, uids=uids, run_id=run_ids.pop(), served_dtype=served.pop(),
                             replay_dtype=rdt.pop(), replay_tf32=tf32.pop(), model=_model())
    thr, val = derive_mixed(cal, load_run_cfg()["g1_mixed"])
    cal["derived_at_build"] = {**thr, "validity": val}          # informational: G1 re-derives from the rows
    Path(a.out).write_text(json.dumps(cal, indent=1))
    print(json.dumps({**thr, **val}, indent=1))


def dryrun(a):
    mc = load_run_cfg()["g1_mixed"]
    rep, xc = _load(a.replay), _load(a.crosscheck)
    _require_aligned(rep, "replay")
    seed = lambda u: int(re.search(r"seed_(\d+)", u).group(1))
    uids = sorted(set(rep) & set(xc))
    ev, od = [u for u in uids if seed(u) % 2 == 0], [u for u in uids if seed(u) % 2]
    cal = calibration_record([_pair(rep[u]["tokens"]) for u in ev],
                             [(rep[u]["tokens"]["replay_logprob"], xc[u]["tokens"]["replay_logprob"]) for u in ev],
                             uids=ev, run_id="dryrun_even_seeds", served_dtype=a.assume_served_dtype,
                             replay_dtype=rep[ev[0]]["tokens"].get("replay_dtype"), model=_model())
    thr, val = derive_mixed(cal, mc)
    judged = [_pair(rep[u]["tokens"]) for u in od]
    j = lambda ps: judge_mixed([row_stats(g, r)[0] for g, r in ps], [row_stats(g, r)[1] for g, r in ps], thr, mc)
    out = {"rules": GATE_RULES_VERSION, "replay": a.replay, "crosscheck": a.crosscheck,
           "served_dtype_assumed": a.assume_served_dtype, "replay_dtype": cal["replay_dtype"],
           "calibration_rows_even_seeds": len(ev), "judged_rows_odd_seeds": len(od), "thresholds": thr,
           "calibration_validity": val, "judged_clean": j(judged),
           "judged_planted": {k: j(v) for k, v in plant_all(judged).items()}}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--transcripts", required=True)
    b.add_argument("--replay", required=True)
    b.add_argument("--crosscheck", required=True)
    b.add_argument("--template-replay")
    b.add_argument("--out", required=True)
    d = sub.add_parser("dryrun")
    d.add_argument("--replay", required=True)
    d.add_argument("--crosscheck", required=True)
    d.add_argument("--assume-served-dtype", required=True)
    d.add_argument("--out", required=True)
    a = ap.parse_args()
    (build if a.cmd == "build" else dryrun)(a)


if __name__ == "__main__":
    main()
