#!/usr/bin/env python3
"""Smoke for the item 6 TorchBackend on Gemma-3-4B-IT (smoke-only profile; same class and tokenizer as the 27B), local,
$0. Mechanics only, nothing analysed: a random unit vector at a 4B layer. Run in fp32 (the pod's replay dtype): in
bf16 on CPU the batch gate shows 0.87 nats of dtype noise between padded and unpadded shapes; fp32 shows 0.0 / 7e-5.

  MODEL_PROFILE=gemma-3-4b-it T1_DTYPE=float32 HF_HUB_OFFLINE=1 uv run --no-project --with pyyaml --with numpy \
      --with pyarrow --with "torch==2.8.0" --with "nnsight<0.8" --with accelerate --with "transformers>=4.50,<4.58" \
      --with pillow --with torchvision python -m probe.smoke_steer_4b
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
    from probe.steer_backend import TorchBackend, EXPECTED_OPTION_IDS
    from probe.run_steering import items_for, msgs_for
    from probe.coherence import coherence_messages, rep4_share
    from probe.tasks import parse_choice
    tol = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())["gates"]["g1_logprob_tol"]
    out = {}
    rep = lambda k, ok, info="": (out.__setitem__(k, (bool(ok), info)), print(("PASS " if ok else "FAIL ") + k + f": {info}"[:300], flush=True))
    t0 = time.time()
    be = TorchBackend(batch_size=4)
    rep("0 model loaded", True, f"{time.time() - t0:.0f}s; hf class {type(be.hf).__name__}; {len(be.hf_layers)} layers")
    try:
        ids = be.option_ids("lottery")
        rep("1 option first-token ids equal run 2's 27B ids (shared Gemma-3 tokenizer)", ids == EXPECTED_OPTION_IDS["lottery"], str(ids))
    except RuntimeError as e:
        rep("1 option first-token ids equal run 2's 27B ids (shared Gemma-3 tokenizer)", False, str(e)); return out
    d = be.hf.config.get_text_config().hidden_size if hasattr(be.hf.config, "get_text_config") else be.hf.config.text_config.hidden_size
    L = 22
    v = np.random.default_rng(0).normal(size=d).astype(np.float32); v /= np.linalg.norm(v)
    its = items_for("lottery")[::30][:6]; msgs = msgs_for("lottery", its)
    r0 = be.readout(msgs, None, None, 0.0); rp = be.readout(msgs, L, v, 0.4); rm = be.readout(msgs, L, v, -0.4)
    # a steered option mass can collapse to ~0 (a random direction at 0.4 x the residual norm breaks the 4B): that is what
    # coherence (a) catches; here P must be in [0, 1] and the masses in [0, 1], and the unsteered mass must be ~1
    rep("2 exact readout: P and masses in [0, 1]; unsteered option mass ~1", all(np.all((x[0] >= 0) & (x[0] <= 1)) and np.all((x[1] >= 0) & (x[1] <= 1 + 1e-9))
        for r in (r0, rp, rm) for x in r.values()) and float(np.min(r0["served"][1])) > 0.9,
        f"P0 served {np.round(r0['served'][0], 3).tolist()} mass0 {np.round(r0['served'][1], 3).tolist()} "
        f"mass(+0.4) {np.round(rp['served'][1], 3).tolist()} mass(-0.4) {np.round(rm['served'][1], 3).tolist()}")
    rep("3 steering changes the readout", float(np.max(np.abs(rp["softmax"][0] - rm["softmax"][0]))) > 1e-4,
        f"max |P(+0.4) - P(-0.4)| softmax {float(np.max(np.abs(rp['softmax'][0] - rm['softmax'][0]))):.4f}")
    pc = be.path_check(msgs[:4], L, v, (0.0, -0.4, 0.4), tol)
    rep("4 HF-hook path == nnsight path at prefill; GPU readout == numpy readout", pc["ok"], json.dumps(pc))
    g = be.generate(msgs[:2], L, v, 0.4, 8)
    rep("5 steered greedy generation (KV cache, hook)", all(len(x) > 0 for x in g), " | ".join(be.decode(x) for x in g))
    gu = be.generate_uncached(msgs[:2], L, v, 0.1, 8); gc = be.generate(msgs[:2], L, v, 0.1, 8)
    gu0 = be.generate_uncached(cm0 := __import__("probe.coherence", fromlist=["x"]).coherence_messages()[:2], None, None, 0.0, 8)
    gc0 = be.generate(cm0, None, None, 0.0, 8)
    rep("5b KV-cached decode == uncached recompute (steered 0.1 and unsteered)", gu == gc and gu0 == gc0,
        f"{[be.decode(x) for x in gc]} | {[be.decode(x) for x in gc0]}")
    seeds = [1_000_000 + i for i in range(4)]
    s1 = be.generate(msgs[:4], L, v, 0.0, 6, sample={"seeds": seeds}); s2 = be.generate(msgs[:4], L, v, 0.0, 6, sample={"seeds": seeds})
    rep("6 seeded sampling is reproducible and parses", s1 == s2 and all(parse_choice("lottery", be.decode(x), it["cond"]) is not None for x, it in zip(s1, its[:4])),
        " | ".join(be.decode(x) for x in s1))
    ft = [be.first_token_class("lottery", x) for x in s1]; pa = [parse_choice("lottery", be.decode(x), it["cond"]) for x, it in zip(s1, its[:4])]
    rep("7 first token decides the parsed answer", all(f == p for f, p in zip(ft, pa) if p is not None and f != "other"), f"{ft} vs {pa}")
    cm = coherence_messages()[:3]
    conts = be.generate(cm, None, None, 0.0, 16); nll = be.cont_nll(cm, conts)
    rep("8 coherence pass: continuation NLLs finite, rep4 computable", all(len(a) == len(b) and all(np.isfinite(b)) for a, b in zip(conts, nll)),
        f"mean NLL {np.mean([x for r in nll for x in r]):.2f}; rep4 {[round(rep4_share(c), 2) for c in conts]}")
    H, ro = be.resid_readout(msgs[:4], [16, 22])
    rep("9 residual capture at two layers + readout in one pass", all(H[l].shape == (4, d) for l in (16, 22)) and np.allclose(ro["served"][0], r0["served"][0][:4], atol=1e-3),
        f"shapes {[H[l].shape for l in (16, 22)]}")
    bg = be.batch_gate("lottery", v, L, tol)
    rep("10 batch gate (batched == unbatched, steering present)", bg.get("ok"), json.dumps({k: bg[k] for k in bg if k.startswith('max_gap') or k.startswith('steering')}))
    n_ok = sum(ok for ok, _ in out.values())
    print(f"\n{n_ok}/{len(out)} passed ({time.time() - t0:.0f}s)")
    return out


if __name__ == "__main__":
    o = main()
    sys.exit(0 if o and all(ok for ok, _ in o.values()) else 1)
