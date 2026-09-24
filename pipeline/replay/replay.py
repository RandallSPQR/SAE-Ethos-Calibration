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


def replay_one(lm, sae, oracle, row, store_rows, oracle_dir, score_positions):
    from .hooks import teacher_forced_forward
    from .sae import encode
    from .oracle import verbalize
    end = scored_index(row) + 1
    fr = teacher_forced_forward(lm, row["messages"][:end], capture_residual=True)
    for pos, feat, act in encode(sae, fr.residual):
        store_rows.append({"uid": row["uid"], "position": int(pos),
                           "in_assistant_span": fr.assistant_span[0] <= pos < fr.assistant_span[1],
                           "feature": int(feat), "activation": float(act), "layer": lm.layer})
    if oracle is not None:
        exps = verbalize(oracle, fr.residual, score_positions(fr.assistant_span))
        (Path(oracle_dir) / f"{row['uid'].replace('/', '__')}.jsonl").write_text(
            "\n".join(json.dumps(e) for e in exps))
    # Build the CONTINUATION-RELATIVE, equal-length G1 arrays. The autoregressive shift is handled here
    # (replay_predicted_ids[k] = argmax of the logits that PREDICT generated position k), so G1 never has
    # to reason about coordinate systems.
    s, e = fr.assistant_span
    gen_ids = (row.get("tokens") or {}).get("sampled_ids")
    row.setdefault("tokens", {})
    row["tokens"].update({
        "ids": fr.token_ids, "assistant_span": list(fr.assistant_span),
        "generated_ids": gen_ids,
        # STUB boundary: replay fills these from the forward. predicted[k] from logits at position s-1+k;
        # replay_logprob[k] = logprob assigned to generated_ids[k]. Lengths == len(gen_ids).
        "replay_predicted_ids": [fr.logits_argmax[s - 1 + k] for k in range(e - s)] if fr.logits_argmax and s > 0 else None,
        "replay_logprob": fr.input_logprobs[s:e] if fr.input_logprobs else None,
        "generation_logprob": (row.get("tokens") or {}).get("sampled_logprobs"),
        # token-level counts for correct E[A] denominators and token-level prefix balancing
        "prompt_token_count": s,
        "decision_token_position": s,
        "assistant_token_count": e - s,
    })
    return row


def mock_replay_one(row, store_rows, rng, n_features=200, l0=8, signal_feature=7, null_feature=3):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None, help="runs/<run_id>/ — sets transcripts/replayed/features under it")
    ap.add_argument("--transcripts", default="transcripts")
    ap.add_argument("--replayed", default=None, help="output dir for replay-derived token metadata")
    ap.add_argument("--features", default="features")
    ap.add_argument("--backend", default="karvonen")
    ap.add_argument("--go", action="store_true", help="load real model/SAE/oracle (needs GPU)")
    ap.add_argument("--mock", action="store_true", help="synthetic tokens + store in the canonical format (no GPU)")
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

    if args.mock:
        import hashlib, random
        lm = sae = oracle = None
    else:
        from .modelload import load_target
        from .sae import load_sae
        from .oracle import load_oracle
        lm, sae, oracle = load_target("target"), load_sae(), load_oracle(args.backend)
    Path(args.features, "oracle").mkdir(parents=True, exist_ok=True)
    # Lineage: NEVER overwrite generation records. Generation transcripts are immutable inputs; replay
    # writes its derived token metadata to a SEPARATE dataset (transcripts/replayed/), joined by uid.
    replayed_dir = Path(args.replayed or (Path(args.transcripts).parent / "replayed"))
    n_in = n_out = 0
    for tf in Path(args.transcripts).rglob("*.jsonl"):
        rows = [json.loads(l) for l in open(tf) if l.strip()]
        store = []
        replay_meta = []
        for row in rows:
            n_in += 1
            if args.mock:
                mock_replay_one(row, store, random.Random(int(hashlib.sha256(row["uid"].encode()).hexdigest()[:8], 16)))
            else:
                replay_one(lm, sae, oracle, row, store, Path(args.features) / "oracle",
                           lambda span: list(range(span[0], span[1])))
            replay_meta.append({"uid": row["uid"], "tokens": row["tokens"]})   # derived, separate record
            n_out += 1
        out = Path(args.features) / rows[0]["scenario"] / rows[0]["variant"]
        out.mkdir(parents=True, exist_ok=True)
        write_parquet(store, out / (tf.stem + ".parquet"))
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
    # cardinality: replayed count must equal input count (no silent drops)
    assert n_in == n_out, f"replay cardinality mismatch: in={n_in} out={n_out}"
    print(f"replay complete: {n_out}/{n_in} continuations -> features={args.features}, "
          f"replayed metadata -> {replayed_dir}")


if __name__ == "__main__":
    main()
