#!/usr/bin/env python3
"""Batch gate: the batched (left-padded, explicit position ids) path must reproduce the unbatched path's
last-position log-probs at T=0 to the G1 tolerance, unsteered AND steered, before it is allowed to
generate. The classic failure is an attention-mask or padding bug that silently changes logits.

  python -m probe.batch_gate --run-dir runs/<run_id> [--task lottery]

Writes probe/<task>/batch_gate.json {ok, max_gap_unsteered, max_gap_steered, argmax_agree}.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from probe.tasks import TASKS, messages, conditions   # noqa: E402


def run_gate(lm, vecs, task, tol, n_prompts=8, strength=0.2):
    from model_io.gemma2 import apply_to_tokenizer
    from replay.hooks import last_logits_batch
    from replay.modelload import residual_module
    from replay.hooks import resid_post, _set_block_output, _val
    t = TASKS[task]; level = t["reference_level"]
    vec = vecs[f"probe_{task}"]; layer = int(vecs[f"probe_{task}__layer"])
    grid = t["grid"]
    prompts = [apply_to_tokenizer(lm.tokenizer, messages(task, grid[(i * 5) % len(grid)], level, conditions(task, 0, i)),
                                  add_generation_prompt=True) for i in range(n_prompts)]
    # reference: unbatched, one prompt at a time, same transform
    def single(ids, st):
        block = lm.model.model.layers[layer]
        with torch.no_grad(), lm.model.trace(torch.tensor([ids])):
            if st != 0.0:
                stream = resid_post(block.output)
                v = torch.as_tensor(np.asarray(vec, dtype=np.float32))
                unit = (v / (v.norm() + 1e-6)).to(stream.device, stream.dtype)
                mean_norm = stream[0, 1:].float().norm(dim=-1).mean().to(stream.dtype)
                _set_block_output(block, stream + st * mean_norm * unit)
            lg = lm.model.output.logits[0, -1].float().save()
        return torch.log_softmax(_val(lg).cpu(), -1)
    rep = {"task": task, "n_prompts": n_prompts, "tol": tol, "lengths": [len(p) for p in prompts]}
    ok = True
    for name, st in (("unsteered", 0.0), ("steered", strength)):
        ref = torch.stack([single(p, st) for p in prompts])
        bat = torch.log_softmax(last_logits_batch(lm, prompts, layer, (vec, st)), -1)
        top = ref.topk(20, dim=-1).indices                                   # compare where mass lives
        gap = float((ref.gather(1, top) - bat.gather(1, top)).abs().max())
        agree = bool((ref.argmax(-1) == bat.argmax(-1)).all())
        rep[f"max_gap_{name}"] = gap; rep[f"argmax_agree_{name}"] = agree
        ok = ok and gap <= tol and agree
        print(f"batch gate [{task}] {name}: max |dlogprob| over top-20 = {gap:.5f} (tol {tol}) argmax agree={agree}")
    rep["ok"] = ok
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--task", default="lottery")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())
    tol = cfg["gates"]["g1_logprob_tol"]
    from replay.modelload import load_target
    lm = load_target("target")
    store = np.load(Path(a.run_dir) / "steering_vectors.npz")
    vecs = {k: store[k] for k in store.files}
    rep = run_gate(lm, vecs, a.task, tol)
    (Path(a.run_dir) / "probe" / a.task / "batch_gate.json").write_text(json.dumps(rep, indent=2))
    print("BATCH_GATE", "OK" if rep["ok"] else "FAIL")
    sys.exit(0 if rep["ok"] else 1)


if __name__ == "__main__":
    main()
