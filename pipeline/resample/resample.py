#!/usr/bin/env python3
"""Decision-point resampling: truncate a transcript at its decision point and draw N continuations
from the target endpoint. This is what turns one Petri trajectory into the labeled destructive/benign
distribution the correlational and steering analyses need. Real code (mockable).

Each continuation becomes a new transcript row (uid ...cNN) with the same prefix, ready for replay.
Cost is metered into the ledger.
"""
import argparse
import json
from pathlib import Path
import yaml

from .target_client import TargetClient

CFG = Path(__file__).resolve().parent.parent / "config"


def resample_row(client, row, n, samp, ledger):
    dp = row["decision_point"]
    prefix = row["messages"][:dp]        # everything up to (not incl.) the decision turn
    out = []
    for k in range(n):
        resp = client.complete(prefix, samp["temperature"], samp["top_p"], samp["max_new_tokens"], seed=k)
        text = resp["text"] if isinstance(resp, dict) else resp
        sampled_ids = resp.get("token_ids") if isinstance(resp, dict) else None
        new = dict(row)
        new["messages"] = prefix + [{"role": "assistant", "content": text, "tool_calls": []}]
        # the new assistant message sits at index dp; score THAT, don't re-derive from decision_point
        new["scored_message_index"] = dp
        new["uid"] = f"{row['scenario']}/seed_{row['seed']:03d}/{row['variant']}/c{k:02d}"
        new["judge"] = None            # re-judged downstream
        # carry the ids the target actually emitted so G0/G1 have a measurement
        new["tokens"] = {"sampled_ids": sampled_ids} if sampled_ids else {}
        new["cost_usd"] = _est_cost(prefix, text)
        out.append(new)
        _log_cost(ledger, new["uid"], new["cost_usd"])
    return out


def _est_cost(prefix, text):
    # rough token-based estimate; real cost comes from the endpoint if it reports usage
    toks = sum(len(m["content"]) for m in prefix) / 4 + len(text) / 4
    return round(toks * 1e-7, 6)


def _log_cost(ledger, uid, cost):
    with open(ledger, "a") as f:
        f.write(json.dumps({"uid": uid, "cost_usd": cost}) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcripts", default="transcripts")
    ap.add_argument("--out", default="transcripts_resampled")
    ap.add_argument("--n", type=int)
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()
    run = yaml.safe_load((CFG / "run.yaml").read_text())
    samp = run["sampling"]
    n = args.n or samp["continuations_per_seed"]
    ledger = run["cost"]["ledger_path"]
    client = TargetClient(mock=args.mock)

    total = 0
    for tf in Path(args.transcripts).rglob("*.jsonl"):
        rows = [json.loads(l) for l in open(tf) if l.strip()]
        arm = "arm_a" if rows[0]["arm"] == "A" else "arm_b"
        outdir = Path(args.out) / arm
        outdir.mkdir(parents=True, exist_ok=True)
        with open(outdir / tf.name, "w") as fh:
            for row in rows:
                for c in resample_row(client, row, n, samp, ledger):
                    fh.write(json.dumps(c) + "\n")
                    total += 1
    print(f"resampled {total} continuations -> {args.out}")


if __name__ == "__main__":
    main()
