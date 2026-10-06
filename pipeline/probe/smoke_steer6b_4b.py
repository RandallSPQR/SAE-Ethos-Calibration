#!/usr/bin/env python3
"""Smoke for the item 6b pod code paths on Gemma-3-4B-IT (smoke-only profile; same class and tokenizer as the 27B), local,
$0, under the 27B replay pins. Mechanics only; nothing is analysed. One process at a time; bf16 to fit the laptop.

  MODEL_PROFILE=gemma-3-4b-it T1_DTYPE=bfloat16 HF_HUB_OFFLINE=1 uv run --no-project --with pyyaml --with numpy \
      --with pyarrow --with "torch==2.8.0" --with "nnsight==0.7.0" --with "accelerate==1.15.0" \
      --with "transformers==5.17.0" --with pillow --with torchvision python -m probe.smoke_steer6b_4b
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import yaml
    from probe.steer_backend import TorchBackend
    from probe import caa as CAA, manipulation as MP
    from probe.tasks import ab_messages, assign_letters, item_key, parse_letter, LETTER_SEED_EVAL
    from probe.run_steering import items_for, msgs_for
    tol = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())["gates"]["g1_logprob_tol"]
    out = {}
    rep = lambda k, ok, info="": (out.__setitem__(k, bool(ok)), print(("PASS " if ok else "FAIL ") + k + f": {info}"[:320], flush=True))
    t0 = time.time()
    be = TorchBackend(batch_size=4)
    rep("0 loaded under the replay pins", True, f"{be.pins}; {time.time() - t0:.0f}s")
    la, lb = be.letter_ids()
    rep("1 'A' / 'B' are single distinct tokens", la != lb, f"{la} {lb}")
    d = be.hf.config.get_text_config().hidden_size
    L = 22
    its = items_for("lottery", 70)[::35][:6]
    letters = assign_letters(items_for("lottery", 70), LETTER_SEED_EVAL)
    ab = [ab_messages(it["n"], it["level"], it["cond"], letters[item_key(it)]) for it in its]
    be.option_ids("lottery"); be.set_ids(la, lb)
    r0 = be.readout(ab, None, None, 0.0)
    rep("2 A/B exact readout at lambda 0: P(A) in [0, 1], A/B mass", np.all((r0["served"][0] >= 0) & (r0["served"][0] <= 1)),
        f"P(A) {np.round(r0['served'][0], 3).tolist()} mass {np.round(r0['served'][1], 3).tolist()}")
    s = be.generate(ab[:4], None, None, 0.0, 6, sample={"seeds": [1_000_000 + i for i in range(4)]})
    first = ["A" if x and x[0] in la else "B" if x and x[0] in lb else None for x in s]
    parsed = [parse_letter(be.decode(x)) for x in s]
    rep("3 sampled A/B answers: first token decides the parsed letter", all(f == p for f, p in zip(first, parsed) if p is not None),
        f"{[be.decode(x) for x in s]}")
    be.option_ids("lottery")
    v = np.random.default_rng(0).normal(size=d).astype(np.float32); v /= np.linalg.norm(v)
    be.absolute = True
    S = 4000.0                     # ~10 % of the 4B's L22 residual norm (~40k): large enough to move log-probs visibly
    pc = be.path_check(msgs_for("lottery", its[:3]), L, v, (0.0, -S, S), tol)
    rep("4 absolute mode: HF-hook path == nnsight path (+-4000)", pc["ok"], json.dumps(pc["gaps"]))
    import torch
    ids3 = be._ids(msgs_for("lottery", its[:3]))
    lp = {s_: torch.log_softmax(be._hf_last_logits(ids3, L, v, s_).float(), -1) for s_ in (-S, S)}
    top = lp[-S].topk(20, dim=-1).indices
    eff = float((lp[S].gather(1, top) - lp[-S].gather(1, top)).abs().max())
    rep("5 absolute steering moves the log-probs (max |dlogprob| over top-20 > 10 x tol)", eff > 10 * tol, f"{eff:.3f}")
    Xt = np.random.default_rng(1).normal(size=(64, d)) * 30
    D, iD = CAA.build(be, L, "ab", Xt, n_items=6); Dw, iDw = CAA.build(be, L, "word", Xt, n_items=6)
    rep("6 CAA builds (A/B and word): unit vectors, token directions logged, hashes", abs(np.linalg.norm(D) - 1) < 1e-4 and abs(np.linalg.norm(Dw) - 1) < 1e-4,
        f"D removed {iD['removed_share']:.3f} cos_token {np.round(iD['cos_raw_with_token_dirs'], 3).tolist()} sha {iD['sha256'][:12]}; "
        f"D_word removed {iDw['removed_share']:.3f}")
    E, U = be.token_rows([la[0], lb[0]])
    rep("7 token rows: embedding and unembedding rows of A and B", E[la[0]].shape == (d,) and U[lb[0]].shape == (d,),
        f"cos(E diff, U diff) {float(np.dot(E[la[0]] - E[lb[0]], U[la[0]] - U[lb[0]]) / np.linalg.norm(E[la[0]] - E[lb[0]]) / np.linalg.norm(U[la[0]] - U[lb[0]])):.3f}")
    mp = [MP.prompt(it) for it in MP.items()[:3]]
    g = be.generate(mp, None, None, 0.0, MP.MAX_NEW)
    parsed = [MP.parse(be.decode(x)) for x in g]
    rep("8 manipulation check generates and parses (accuracy reported, not judged on the 4B)", True,
        f"{[p[:2] for p in parsed]} | {be.decode(g[0])[:120]!r}")
    print(f"\n{sum(out.values())}/{len(out)} passed ({time.time() - t0:.0f}s)")
    return out


if __name__ == "__main__":
    o = main()
    sys.exit(0 if all(o.values()) else 1)
