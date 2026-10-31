#!/usr/bin/env python3
"""Transgression-span scoring (PLAN_27B span decision 1; queue item 4). Same code path as the decision-span replay:
replay._prefix_ids + the turn's own sampled ids through hooks.teacher_forced_forward, so the G1-checked observation path is
the one every span activation comes from.

One forward per (row, span). A span is an assistant turn located by harness.transgression_spans (act / report / attempt
on transgression rows; the role-matched counterparts submit / commit / legacy_removal on benign control rows). The input
is what generation conditioned on when it sampled that turn: the conversation BEFORE the turn, text-serialized by the
canonical serializer (the harness sent earlier turns back as text), then the turn's own `sampled_ids`, cut at the first
stop id. Every T3 assistant turn carries its sampled ids and generation logprobs, so G1 is checkable on every span
(the arrays are written beside the activations).

Per span it records: the primary SAE's (layer L) in-span feature sums and token count; the secondary SAE's (profile
sae_secondary) sums from the same pass; the raw residual at each probe layer (profile probe.layer_candidates), mean and
last token over the span, for later probes; the G1 arrays. "Layer L" is the output of decoder block L =
transformers' hidden_states[L + 1] (the ladder's identity; the smoke checks it against output_hidden_states).

Guards (Randall, 2026-10-01):
  - refuses any input or output path naming the held-out set (HELDOUT_MARKERS), and any spans file not written by the
    locator;
  - refuses to run without a hand-check RESULT file that records a PASS (no analysis-set span is read before it);
  - asserts every captured decoder block is a LANGUAGE-model layer (model.language_model.layers.<i>), never a module of
    the vision tower (Gemma3ForConditionalGeneration also has SigLIP encoder layers with the same shape names).

  python -m replay.span_score --run-dir <run> --spans <dir>/transgression_spans.jsonl \\
      --control-spans <dir>/control_spans.jsonl --hand-check <dir>/hand_check/RESULT.md --out <run>/span_features --go
"""
import argparse
import json
from pathlib import Path

import numpy as np

HELDOUT_MARKERS = ("held_out", "heldout", "held-out", "matched_pair", "matched-pair", "matchedpair")
SPAN_FIELDS = ("act", "report", "attempt")


def refuse_heldout(*paths):
    for p in paths:
        if p is None:
            continue
        s = str(Path(p).resolve()).lower()
        if any(m in s for m in HELDOUT_MARKERS):
            raise SystemExit(f"REFUSED: {p} names the held-out set; span scoring runs on dev data only until the span "
                             f"pre-registration is committed (PLAN_27B, rules for the held-out set)")


def require_handcheck(path):
    if not path or not Path(path).exists():
        raise SystemExit("REFUSED: no hand-check RESULT file; no analysis-set span is read before the locator hand-check passes")
    if "**PASS.**" not in Path(path).read_text():
        raise SystemExit(f"REFUSED: {path} does not record a PASS")


def span_jobs(spans_path, control_path=None):
    """{uid: [(span_name, message_index)]}: located, uncontaminated transgression spans plus benign-control counterparts."""
    jobs = {}
    for l in open(spans_path):
        if not l.strip():
            continue
        r = json.loads(l)
        if "locator_version" not in r:
            raise SystemExit(f"REFUSED: {spans_path} is not a locator output (no locator_version)")
        if r["status"] != "ok" or r.get("cell_excluded"):
            continue
        for f in SPAN_FIELDS:
            s = r.get(f)
            if s and s.get("message_index") is not None:
                jobs.setdefault(r["uid"], []).append((f, int(s["message_index"])))
    if control_path:
        for l in open(control_path):
            if not l.strip():
                continue
            c = json.loads(l)
            for role, s in (c.get("roles") or {}).items():
                if s:
                    jobs.setdefault(c["uid"], []).append((f"control_{role}", int(s["message_index"])))
    for uid in jobs:                                  # one pass per distinct message: act and attempt can coincide
        seen, out = {}, []
        for name, k in jobs[uid]:
            seen.setdefault(k, []).append(name)
        jobs[uid] = [("+".join(v), k) for k, v in sorted(seen.items())]
    return jobs


def torch_module(envoy):
    """The torch module behind an nnsight envoy (0.4: ._module; 0.5+: ._module as well); a plain module passes through."""
    return getattr(envoy, "_module", envoy)


def assert_language_layers(lm, layer_indices):
    """Every captured block must be model.language_model.layers.<i> (or model.layers.<i> on a text-only model), never a
    vision-tower module. Returns {layer: module name}."""
    from .modelload import decoder_layers
    hf = torch_module(lm.model)
    names = {id(m): n for n, m in hf.named_modules()}
    layers = decoder_layers(lm)
    out = {}
    for L in layer_indices:
        mod = torch_module(layers[L])
        n = names.get(id(mod))
        if n is None:
            raise AssertionError(f"layer {L}: captured module is not inside the loaded model")
        ok = n.endswith(f"layers.{L}") and ("language_model" in n or n.startswith(("model.layers.", "layers.")))
        if not ok or "vision" in n or "siglip" in n.lower():
            raise AssertionError(f"layer {L}: captured module {n!r} is not a language-model decoder layer")
        out[L] = n
    return out


def span_inputs(lm, row, k):
    """(input_ids, (start, end), core_ids, gen_logprobs, gen_margin) for assistant message k: the text-serialized
    conversation before it (canonical serializer, generation prompt) + its own sampled ids cut at the first stop."""
    import modelcfg
    from .replay import _prefix_ids
    m = row["messages"][k]
    if m.get("role") != "assistant":
        raise ValueError(f"{row['uid']} message {k} is not an assistant turn")
    t = m.get("tokens") or {}
    sampled = t.get("sampled_ids")
    if not sampled:
        raise ValueError(f"{row['uid']} message {k} has no sampled_ids: span not G1-checkable, refused")
    prefix = list(_prefix_ids(lm, row["messages"][:k]))
    stops = set(modelcfg.stop_token_ids())
    cut = next((i for i, x in enumerate(sampled) if x in stops), len(sampled))
    core = list(sampled[:cut])
    lps, mrg = t.get("sampled_logprobs"), t.get("sampled_top2_margin")
    return prefix + core, (len(prefix), len(prefix) + len(core)), core, (lps[:cut] if lps else None), (mrg[:cut] if mrg else None)


def score_span(lm, sae, row, name, k, extra_layers, secondary=None, probe_layers=()):
    """One teacher-forced pass for one span; returns (record, residual summaries)."""
    from .hooks import teacher_forced_forward
    from .sae import encode
    ids, (s, e), core, gen_lp, gen_mrg = span_inputs(lm, row, k)
    fr = teacher_forced_forward(lm, None, capture_residual=True, input_ids=ids, span=(s, e), extra_layers=extra_layers)
    resid = {lm.layer: np.asarray(fr.residual)}
    for key, v in (fr.extra or {}).items():
        if key.startswith("resid_L"):
            resid[int(key[7:])] = np.asarray(v)
    sums = {}
    if sae is not None:
        for _, f, a in encode(sae, resid[lm.layer][s:e]):
            sums[f] = sums.get(f, 0.0) + a
    sums2 = {}
    if secondary is not None:
        for _, f, a in encode(secondary["sae"], resid[secondary["layer"]][s:e]):
            sums2[f] = sums2.get(f, 0.0) + a
    pooled = {L: {"mean": resid[L][s:e].mean(0).astype(np.float32), "last": resid[L][e - 1].astype(np.float32)}
              for L in probe_layers if L in resid and e > s}
    rec = {"uid": row["uid"], "span": name, "message_index": k, "span_tokens": e - s, "prefix_tokens": s,
           "layer": lm.layer, "feature_sums": {str(f): v for f, v in sums.items()},
           "secondary_layer": (secondary or {}).get("layer"), "secondary_feature_sums": {str(f): v for f, v in sums2.items()},
           # G1 arrays, continuation-relative and equal length (the decision-span replay's contract)
           "generated_ids": core, "generation_logprob": gen_lp, "sampled_top2_margin": gen_mrg,
           "replay_logprob": fr.input_logprobs[s:e] if fr.input_logprobs else None,
           "replay_predicted_ids": [fr.logits_argmax[s - 1 + i] for i in range(e - s)] if fr.logits_argmax and s > 0 else None,
           "replay_top2_ids": [fr.logits_top2[s - 1 + i] for i in range(e - s)] if fr.logits_top2 and s > 0 else None}
    return rec, pooled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="a run dir with generation/arm_a/*.jsonl (dev only)")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--control-spans", default=None)
    ap.add_argument("--hand-check", required=True, help="the locator hand-check RESULT.md (must record a PASS)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--uids", default=None, help="file of uids to score (smoke); default every row with a span")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--synthetic-sae", type=int, default=0,
                    help="SMOKE ONLY: a random JumpReLU-shaped SAE of this width instead of the profile's (no SAE download)")
    ap.add_argument("--device", default=None, help="load device / device_map override (smoke: cpu)")
    args = ap.parse_args()
    refuse_heldout(args.run_dir, args.spans, args.control_spans, args.out, args.uids)
    require_handcheck(args.hand_check)
    jobs = span_jobs(args.spans, args.control_spans)
    if args.uids:
        keep = {l.strip() for l in open(args.uids) if l.strip()}
        jobs = {u: v for u, v in jobs.items() if u in keep}
    print(f"{sum(len(v) for v in jobs.values())} spans in {len(jobs)} rows")
    if not args.go:
        print("[dry run] pass --go to load the model")
        return
    import os
    import modelcfg
    from .modelload import load_target
    os.environ.setdefault("T1_DTYPE", modelcfg.replay_cfg()["dtype"])
    lm = load_target("target", device=args.device or "cuda")
    probe_layers = [int(x) for x in (modelcfg.probe_cfg().get("layer_candidates") or [])]
    sec_block = modelcfg.secondary_sae()
    sec_layer = int(sec_block["layer"]) if sec_block else None
    extra = sorted({L for L in probe_layers + ([sec_layer] if sec_layer is not None else []) if L != lm.layer})
    names = assert_language_layers(lm, [lm.layer] + extra)
    print("captured decoder blocks:", names)
    if args.synthetic_sae:
        from .sae_synthetic import SyntheticSAE
        sae = SyntheticSAE(lm.d_model, args.synthetic_sae, seed=1)
        secondary = {"layer": sec_layer, "sae": SyntheticSAE(lm.d_model, args.synthetic_sae, seed=2)} if sec_layer is not None else None
    else:
        from .sae import load_sae
        dev = "cuda" if (args.device or "cuda") in ("cuda", "auto") else args.device
        sae = load_sae(device=dev)
        secondary = {"layer": sec_layer, "sae": load_sae(device=dev, block=sec_block)} if sec_block else None
    rows = {}
    for f in sorted(Path(args.run_dir).glob("generation/arm_a/*.jsonl")):
        for l in open(f):
            if l.strip():
                r = json.loads(l)
                if r["uid"] in jobs:
                    rows[r["uid"]] = r
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    recs, pooled_idx, arrays = [], [], {}
    n = 0
    for uid in sorted(jobs):
        for name, k in jobs[uid]:
            rec, pooled = score_span(lm, sae, rows[uid], name, k, extra, secondary=secondary, probe_layers=probe_layers)
            recs.append(rec)
            for L, d in pooled.items():
                key = f"{len(pooled_idx)}"
                arrays[f"{key}_mean"], arrays[f"{key}_last"] = d["mean"], d["last"]
                pooled_idx.append({"key": key, "uid": uid, "span": name, "message_index": k, "layer": L})
            n += 1
            if args.limit and n >= args.limit:
                break
        if args.limit and n >= args.limit:
            break
    with open(out / "span_scores.jsonl", "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    np.savez_compressed(out / "span_residuals.npz", **arrays)
    (out / "span_residuals_index.json").write_text(json.dumps(pooled_idx))
    (out / "SPAN_SCORE.json").write_text(json.dumps({
        "spans": len(recs), "rows": len({r['uid'] for r in recs}), "primary_layer": lm.layer, "secondary_layer": sec_layer,
        "probe_layers": probe_layers, "captured_blocks": {str(k): v for k, v in names.items()},
        "replay_dtype": os.environ.get("T1_DTYPE"), "synthetic_sae": args.synthetic_sae or None,
        "spans_file": str(args.spans), "control_spans_file": str(args.control_spans), "hand_check": str(args.hand_check)}, indent=1))
    print(f"scored {len(recs)} spans -> {out}")


if __name__ == "__main__":
    main()
