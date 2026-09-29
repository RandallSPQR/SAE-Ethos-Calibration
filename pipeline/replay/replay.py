#!/usr/bin/env python3
"""Stage 2 driver: transcript -> teacher-forced forward -> sparse feature store (+ oracle). # STUB core.

Reads transcripts (contract), loads model/SAE/oracle once, teacher-forces each continuation, writes:
  features/<scenario>/<variant>/*.parquet   (uid, position, in_assistant_span, feature, activation, layer)
  features/oracle/<uid>.jsonl               (verbalizer explanations)
and back-fills transcript['tokens'] with ids + sampled_ids for G1.

The heavy loads are stubs; the orchestration, the row shape, and the parquet writer are real so the
store schema is exercised by the fixture path.
"""
import argparse
import json
from pathlib import Path


def scored_index(row):
    """The assistant message whose activations we score. Explicit field wins; fall back to
    decision_point only for legacy original transcripts. Never decision_point + 1."""
    return row.get("scored_message_index", row["decision_point"])


# rules 2026-09-29.2: the G1 calibration needs one realistic defect that must FAIL G1: a replay whose prefix drops the
# newline after <start_of_turn>model (a one-character template error). REPLAY_TEMPLATE_DEFECT=drop_model_newline selects
# it; every other replay leaves it unset, and rows record which one ran.
import os as _os
TEMPLATE_DEFECT = _os.environ.get("REPLAY_TEMPLATE_DEFECT") or None


def _prefix_ids(lm, messages):
    import modelcfg
    ser = modelcfg.serializer()
    if TEMPLATE_DEFECT is None:
        return ser.apply_to_tokenizer(lm.tokenizer, messages, add_generation_prompt=True)
    if TEMPLATE_DEFECT != "drop_model_newline":
        raise ValueError(f"unknown REPLAY_TEMPLATE_DEFECT {TEMPLATE_DEFECT!r}")
    text = ser.serialize_messages(messages, add_generation_prompt=True)
    assert text.endswith("<start_of_turn>model\n")
    return lm.tokenizer(text[:-1], add_special_tokens=True)["input_ids"]


def replay_one(lm, sae, oracle, row, store_rows, oracle_dir, score_positions, uidsum_rows=None, sample=None, secondary=None,
               extra_hooks=None):
    from .hooks import teacher_forced_forward
    from .sae import encode
    from .oracle import verbalize
    end = scored_index(row) + 1
    sampled = (row.get("tokens") or {}).get("sampled_ids")
    overran = False
    if sampled:
        # Audit blockers 1 and 3 (2026-09-28): teacher-force the ids the model actually sampled, after the prefix the
        # canonical serializer produces, instead of re-tokenizing the decoded text (a trailing newline merged into the
        # turn suffix on 8% of T3 rows; 25 rows were shifted by an INTERIOR <end_of_turn> the model ran past). The span is
        # cut at the first turn-end / EOS token; an interior one marks the continuation as overran_turn.
        import modelcfg
        prefix = _prefix_ids(lm, row["messages"][:end - 1])
        stops = [i for i, t in enumerate(sampled) if t in set(modelcfg.stop_token_ids())]
        cut = stops[0] if stops else len(sampled)
        overran = bool(stops) and cut < len(sampled) - 3
        core = list(sampled[:cut])
        fr = teacher_forced_forward(lm, None, capture_residual=True, extra_hooks=extra_hooks,
                                    input_ids=list(prefix) + core, span=(len(prefix), len(prefix) + len(core)),
                                    extra_layers=([secondary["layer"]] if secondary else ()))
    else:
        fr = teacher_forced_forward(lm, row["messages"][:end], capture_residual=True, extra_hooks=extra_hooks,
                                    extra_layers=([secondary["layer"]] if secondary else ()))
    s0, e0 = fr.assistant_span
    sums = {}
    for pos, feat, act in encode(sae, fr.residual):
        in_span = s0 <= pos < e0
        store_rows.append({"uid": row["uid"], "position": int(pos), "in_assistant_span": in_span,
                           "feature": int(feat), "activation": float(act), "layer": lm.layer})
        if in_span:
            sums[feat] = sums.get(feat, 0.0) + float(act)
    if secondary is not None:
        # the pre-registered secondary SAE (profile sae_secondary), read from the SAME forward pass at its own layer and
        # stored apart (features_L<layer>/): never pooled with the primary, analysed as its own named layer
        L2, sums2 = secondary["layer"], {}
        for pos, feat, act in encode(secondary["sae"], fr.extra[f"resid_L{L2}"]):
            in_span = s0 <= pos < e0
            secondary["store"].append({"uid": row["uid"], "position": int(pos), "in_assistant_span": in_span,
                                       "feature": int(feat), "activation": float(act), "layer": L2})
            if in_span:
                sums2[feat] = sums2.get(feat, 0.0) + float(act)
        for feat, tot in sums2.items():
            secondary["uidsums"].append({"uid": row["uid"], "feature": int(feat), "sum_act": tot, "span_tokens": e0 - s0})
    if uidsum_rows is not None:
        # per-uid, in-span sums: E[A] numerators for every feature at once (analyze.discover reads these, not the
        # position store; 2026-09-28 pre-registration statistic 4)
        for feat, tot in sums.items():
            uidsum_rows.append({"uid": row["uid"], "feature": int(feat), "sum_act": tot, "span_tokens": e0 - s0})
    if sample is not None:
        sample.append({"uid": row["uid"], "resid": fr.residual[s0:e0], "span_ids": fr.token_ids[s0:e0],
                       "extra": {k: v[s0:e0] for k, v in (fr.extra or {}).items()}})
    if oracle is not None:
        exps = verbalize(oracle, fr.residual, score_positions(fr.assistant_span))
        (Path(oracle_dir) / f"{row['uid'].replace('/', '__')}.jsonl").write_text(
            "\n".join(json.dumps(e) for e in exps))
    # Build the CONTINUATION-RELATIVE, equal-length G1 arrays. The autoregressive shift is handled here
    # (replay_predicted_ids[k] = argmax of the logits that PREDICT generated position k), so G1 never has
    # to reason about coordinate systems.
    s, e = fr.assistant_span
    gen_ids = (row.get("tokens") or {}).get("sampled_ids")
    gen_lps = (row.get("tokens") or {}).get("sampled_logprobs")
    gen_mrg = (row.get("tokens") or {}).get("sampled_top2_margin")
    tail = None
    if sampled:
        # raw-id path: the span IS the sampled core by construction; the tail is whatever followed the first stop
        tail = list(sampled[e - s:]) or None
        gen_ids = list(sampled[: e - s])
        gen_lps = gen_lps[: e - s] if gen_lps else gen_lps
        gen_mrg = gen_mrg[: e - s] if gen_mrg else gen_mrg
    elif gen_ids and len(gen_ids) > (e - s):
        # vLLM's sampled ids carry the turn suffix / EOS (Gemma-2: <end_of_turn>, "\n", <eos> = 107/108/1) that the replay span
        # excludes by construction; align the G1 arrays to the span (T3 white-box 2026-09-28: every one of 2,289 rows was
        # 1-3 tokens longer and G1 failed on presence, not fidelity). The raw tail is kept for the record.
        tail = gen_ids[e - s:]
        gen_ids = gen_ids[: e - s]
        gen_lps = gen_lps[: e - s] if gen_lps else gen_lps
        gen_mrg = gen_mrg[: e - s] if gen_mrg else gen_mrg
    row.setdefault("tokens", {})
    row["tokens"].update({
        "ids": fr.token_ids, "assistant_span": list(fr.assistant_span),
        "generated_ids": gen_ids, "generated_ids_raw_tail": tail,
        "sampled_top2_margin": gen_mrg,
        "span_ids_equal_sampled": (gen_ids == fr.token_ids[s:e]) if gen_ids else None,
        "overran_turn": overran, "replay_path": "sampled_ids" if sampled else "retokenized",
        "replay_dtype": REPLAY_DTYPE,        # 2026-09-29: recorded so G1 can refuse a replay that ran in another dtype than generation
        "replay_template_defect": TEMPLATE_DEFECT,
        "replay_tf32": _os.environ.get("T1_TF32") == "1",   # rules 2026-09-29.3: TF32 matmuls are part of the replay's identity   # rules 2026-09-29.2: set ONLY on the G1 calibration's planted-defect replay
        # STUB boundary: replay fills these from the forward. predicted[k] from logits at position s-1+k;
        # replay_logprob[k] = logprob assigned to generated_ids[k]. Lengths == len(gen_ids).
        "replay_predicted_ids": [fr.logits_argmax[s - 1 + k] for k in range(e - s)] if fr.logits_argmax and s > 0 else None,
        "replay_top2_ids": [fr.logits_top2[s - 1 + k] for k in range(e - s)] if fr.logits_top2 and s > 0 else None,
        "replay_logprob": fr.input_logprobs[s:e] if fr.input_logprobs else None,
        "generation_logprob": gen_lps,
        # token-level counts for correct E[A] denominators and token-level prefix balancing
        "prompt_token_count": s,
        "decision_token_position": s,
        "assistant_token_count": e - s,
    })
    return row


def mock_replay_one(row, store_rows, rng, n_features=200, l0=8, signal_feature=7, null_feature=3, uidsum_rows=None):
    """--mock: synthetic token metadata and a synthetic sparse store in the CANONICAL row shape, so the
    white-box path (replay -> store -> analyze.effects -> G8) runs on behavioral transcripts before any real
    activation exists (2026-09-24). Token counts come from the transcript text (~4 chars/token); the store
    fires `l0` features per assistant position out of `n_features`, log-normal activations; `signal_feature`
    is planted to fire more on destructive-taxonomy continuations (the effect path must find it) and
    `null_feature` is label-independent (G8's null must stay near zero). Deterministic per uid."""
    import math
    msgs = row["messages"]
    dp = scored_index(row)
    prompt_chars = sum(len(str(m.get("content", ""))) for m in msgs[:dp])
    s = max(8, prompt_chars // 4)
    assistant_chars = len(str(msgs[dp].get("content", ""))) if dp < len(msgs) else 40
    n_tok = max(4, assistant_chars // 4)
    e = s + n_tok
    tax = (row.get("judge") or {}).get("taxonomy", "benign")
    destructive = tax in ("deception_concealment", "destructive", "task_cheating", "safeguard_bypass", "overeager")
    for pos in range(s, e):
        feats = set(rng.sample(range(n_features), l0))
        if rng.random() < (0.6 if destructive else 0.15):
            feats.add(signal_feature)
        if rng.random() < 0.3:
            feats.add(null_feature)
        for f in feats:
            base = math.exp(rng.gauss(0.0, 0.5))
            if f == signal_feature and destructive:
                base *= 2.0
            store_rows.append({"uid": row["uid"], "position": pos, "in_assistant_span": True,
                               "feature": int(f), "activation": float(base), "layer": 31})
    if uidsum_rows is not None:
        agg = {}
        for r in store_rows:
            if r["uid"] == row["uid"] and r["in_assistant_span"]:
                agg[r["feature"]] = agg.get(r["feature"], 0.0) + r["activation"]
        for f, tot in agg.items():
            uidsum_rows.append({"uid": row["uid"], "feature": int(f), "sum_act": tot, "span_tokens": n_tok})
    gen_ids = (row.get("tokens") or {}).get("sampled_ids") or list(range(1000, 1000 + n_tok))
    row.setdefault("tokens", {})
    row["tokens"].update({"ids": list(range(e)), "assistant_span": [s, e], "generated_ids": gen_ids,
                          "replay_predicted_ids": gen_ids, "replay_logprob": [-0.1] * len(gen_ids),
                          "generation_logprob": (row.get("tokens") or {}).get("sampled_logprobs"),
                          "prompt_token_count": s, "decision_token_position": s, "assistant_token_count": n_tok,
                          "mock": True})
    return row


def write_parquet(rows, out_path):
    try:
        import pyarrow as pa, pyarrow.parquet as pq   # noqa
        import pyarrow as pa
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, out_path)
    except ImportError:
        # fallback: JSONL so the scaffold runs without pyarrow
        Path(out_path).with_suffix(".jsonl").write_text("\n".join(json.dumps(r) for r in rows))


REPLAY_DTYPE = None      # set in main() for --go; None for --mock (no model)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None, help="runs/<run_id>/ — sets transcripts/replayed/features under it")
    ap.add_argument("--transcripts", default="transcripts")
    ap.add_argument("--replayed", default=None, help="output dir for replay-derived token metadata")
    ap.add_argument("--features", default="features")
    ap.add_argument("--backend", default="karvonen")
    ap.add_argument("--go", action="store_true", help="load real model/SAE/oracle (needs GPU)")
    ap.add_argument("--mock", action="store_true", help="synthetic tokens + store in the canonical format (no GPU)")
    ap.add_argument("--oracle", action="store_true", help="verbalize every assistant position (slow); default off: the oracle "
                                                          "runs on survivors only (pre-registration 2026-09-28)")
    ap.add_argument("--scenarios", default=None, help="comma-separated scenario ids to replay (default all)")
    ap.add_argument("--limit", type=int, default=None, help="replay at most N continuations per file (smoke test)")
    ap.add_argument("--instrument-sample", type=int, default=0,
                    help="capture N assistant spans (round-robin over files) with decoy hooks and build the G2/G3/G5 "
                         "reports on real spans under features/ (replay.instrument)")
    ap.add_argument("--identity-from", default=None, help="T1 sae_health.json whose tensor-identity block is carried into G2")
    args = ap.parse_args()

    if args.run_dir:
        from pathlib import Path as _P
        args.transcripts = str(_P(args.run_dir) / "generation")
        args.replayed = str(_P(args.run_dir) / "replay")
        args.features = str(_P(args.run_dir) / "features")

    if not args.go and not args.mock:
        print("[dry run] replay wiring OK. Pass --go on a GPU box with nnsight+sae-lens+peft installed, "
              "or --mock for a synthetic store in the canonical format.")
        print("Would process:",
              sum(1 for _ in Path(args.transcripts).rglob("*.jsonl")), "transcript files.")
        return

    secondary = None
    if args.mock:
        import hashlib, random
        lm = sae = oracle = None
    else:
        # 2026-09-29 (deep resample attempt 1): the replay dtype came from T1_DTYPE and defaulted to models.yaml's bfloat16;
        # the deep driver did not export it, and the replay ran bf16 against fp32 generation (per-row worst gap median 0.12
        # nats vs 0.004 on the fp32 white-box replay). Real replay is float32 unless T1_DTYPE says otherwise, and says so.
        import os
        import modelcfg
        os.environ.setdefault("T1_DTYPE", modelcfg.replay_cfg()["dtype"])
        global REPLAY_DTYPE
        REPLAY_DTYPE = os.environ["T1_DTYPE"]
        print(f"replay dtype: {REPLAY_DTYPE} (T1_DTYPE)")
        from .modelload import load_target
        from .sae import load_sae
        from .oracle import load_oracle
        lm, sae = load_target("target"), load_sae()
        oracle = load_oracle(args.backend, lm=lm) if args.oracle else None
        # REPLAY_SECONDARY=0 skips the secondary layer (the G1 calibration's crosscheck and template-defect replays need none)
        sec_block = modelcfg.secondary_sae() if os.environ.get("REPLAY_SECONDARY", "1") != "0" else None
        if sec_block:
            secondary = {"layer": int(sec_block["layer"]), "sae": load_sae(block=sec_block), "block": sec_block}
            print(f"secondary SAE: layer {secondary['layer']} {sec_block['release']}/{sec_block['sae_id']} (same pass)")
    Path(args.features, "oracle").mkdir(parents=True, exist_ok=True)
    only = set(args.scenarios.split(",")) if args.scenarios else None
    sample = [] if args.instrument_sample else None
    per_file_sample = None
    extra_hooks = None
    if sample is not None and not args.mock:
        from .modelload import hook_reader
        from .sae import sae_cfg
        s_cfg = sae_cfg()
        extra_hooks = [hook_reader(h) for h in s_cfg.get("hook_candidates", [])]
    # Lineage: NEVER overwrite generation records. Generation transcripts are immutable inputs; replay
    # writes its derived token metadata to a SEPARATE dataset (transcripts/replayed/), joined by uid.
    replayed_dir = Path(args.replayed or (Path(args.transcripts).parent / "replayed"))
    n_in = n_out = 0
    import time as _time
    t0 = _time.time()
    files = sorted(Path(args.transcripts).rglob("*.jsonl"))
    if sample is not None:
        per_file_sample = max(1, -(-args.instrument_sample // max(1, len(files))))
    for tf in files:
        rows = [json.loads(l) for l in open(tf) if l.strip()]
        if not rows or (only is not None and rows[0]["scenario"] not in only):
            continue
        if args.limit:
            rows = rows[: args.limit]
        store, uidsums = [], []
        store2, uidsums2 = [], []
        replay_meta = []
        taken = 0
        for row in rows:
            n_in += 1
            take = sample is not None and taken < per_file_sample and len(sample) < args.instrument_sample
            if args.mock:
                mock_replay_one(row, store, random.Random(int(hashlib.sha256(row["uid"].encode()).hexdigest()[:8], 16)),
                                uidsum_rows=uidsums)
            else:
                replay_one(lm, sae, oracle, row, store, Path(args.features) / "oracle",
                           lambda span: list(range(span[0], span[1])), uidsum_rows=uidsums,
                           sample=(sample if take else None), extra_hooks=(extra_hooks if take else None),
                           secondary=(dict(secondary, store=store2, uidsums=uidsums2) if secondary else None))
            taken += take
            replay_meta.append({"uid": row["uid"], "tokens": row["tokens"]})   # derived, separate record
            n_out += 1
            if n_out % 50 == 0:
                print(f"  replayed {n_out} ({(_time.time() - t0) / n_out:.2f} s/continuation)", flush=True)
        out = Path(args.features) / rows[0]["scenario"] / rows[0]["variant"]
        out.mkdir(parents=True, exist_ok=True)
        write_parquet(store, out / (tf.stem + ".parquet"))
        write_parquet(uidsums, out / (tf.stem + "_uidsums.parquet"))
        if secondary:
            out2 = Path(f"{Path(args.features)}_L{secondary['layer']}") / rows[0]["scenario"] / rows[0]["variant"]
            out2.mkdir(parents=True, exist_ok=True)
            write_parquet(store2, out2 / (tf.stem + ".parquet"))
            write_parquet(uidsums2, out2 / (tf.stem + "_uidsums.parquet"))
            (out2.parents[1] / "SECONDARY.json").write_text(json.dumps(
                {"role": "secondary (pre-registered)", "layer": secondary["layer"], **{k: secondary["block"].get(k) for k in
                 ("release", "sae_id", "hf_folder", "revision", "params_sha256")}}, indent=1))
        rd = replayed_dir / tf.relative_to(args.transcripts).parent
        rd.mkdir(parents=True, exist_ok=True)
        with open(rd / tf.name, "w") as fh:
            for m in replay_meta:
                fh.write(json.dumps(m) + "\n")
    if args.mock:
        # a mock concept index: the planted signal and a null feature, selected on DISCOVER seeds only
        seeds = sorted({r["seed"] for tf in Path(args.transcripts).rglob("*.jsonl") for r in map(json.loads, filter(str.strip, open(tf)))})
        (Path(args.features) / "concept_index.json").write_text(json.dumps({
            "mock_planted_signal": {"feature": 7, "selection_seeds": seeds, "source": "mock"},
            "mock_null_feature": {"feature": 3, "selection_seeds": seeds, "source": "mock"}}, indent=1))
        (Path(args.features) / "MOCK").write_text("synthetic store from replay.replay --mock; not activations\n")
    if sample:
        from . import instrument
        import modelcfg
        cal = modelcfg.models().get("calibration", {})
        feat_dir = Path(args.features)
        print(f"instrument checks on {len(sample)} real spans ...", flush=True)
        instrument.build_sae_health(lm, sae, sample, feat_dir / "sae_health.json", identity_from=args.identity_from)
        if cal.get("code_feature_index") is None:     # per-SAE anchor not chosen yet: G3 reads "missing", not a 9B index
            (feat_dir / "known_answer_report.json").write_text(json.dumps({"error": "no calibration.code_feature_index in the "
                                                                          "model profile; G3 skipped and reported as missing"}))
        else:
            instrument.build_known_answer(sae, lm.tokenizer, sample, feat_dir / "known_answer_report.json",
                                          cal["code_feature_index"], cal.get("extra_feature_indices", []))
        try:
            instrument.build_oracle_calibration(lm, lm.tokenizer, sample, feat_dir / "oracle_calibration.json", backend=args.backend)
        except Exception as ex:                       # noqa: BLE001 — the oracle is the last, optional load
            import traceback; traceback.print_exc()
            (feat_dir / "oracle_calibration.json").write_text(json.dumps({"error": f"{type(ex).__name__}: {ex}"}))
    # cardinality: replayed count must equal input count (no silent drops)
    assert n_in == n_out, f"replay cardinality mismatch: in={n_in} out={n_out}"
    print(f"replay complete: {n_out}/{n_in} continuations -> features={args.features}, "
          f"replayed metadata -> {replayed_dir}")


if __name__ == "__main__":
    main()
