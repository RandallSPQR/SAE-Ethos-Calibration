#!/usr/bin/env python3
"""P2: residual at the FINAL PROMPT TOKEN (the position that predicts the first answer token) for every
trial, at each layer in probe.layer_candidates, via the ONE serializer and the ONE forward
(replay.hooks.build_input_ids + teacher_forced_forward, resid_post() — never bare output[0]).

  python -m probe.extract --run-dir runs/<run_id> [--mock]

Writes runs/<run_id>/probe/<task>/activations.npz: X_<layer> [n, d_model] (prompt-final position),
Xfirst_<layer> [n, d_model] (first answer token position), y [n], uid [n], param [n]; and meta.json.
--mock synthesizes activations with a planted direction correlated with y so P3/G9 run on CPU.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CFG = ROOT / "config"


def probe_cfg():
    return yaml.safe_load((CFG / "run.yaml").read_text())["probe"]


def load_trials(d):
    rows = [json.loads(l) for l in open(Path(d) / "trials.jsonl") if l.strip()]
    seen = set()
    for r in rows:
        if r["uid"] in seen:
            raise RuntimeError(f"duplicate uid {r['uid']} in {d}/trials.jsonl")
        seen.add(r["uid"])
    return [r for r in rows if r["label"] is not None]


def mock_activations(rows, layers, d_model=64, seed=0):
    rng = np.random.default_rng(seed)
    y = np.array([r["label"] for r in rows], dtype=np.int64)
    out = {}
    for L in layers:
        w = rng.normal(size=d_model); w /= np.linalg.norm(w)
        strength = 2.0 if L == layers[-1] else 1.0
        X = rng.normal(size=(len(rows), d_model)) + strength * (2 * y[:, None] - 1) * w[None, :]
        out[f"X_{L}"] = X.astype(np.float32)
        out[f"Xfirst_{L}"] = (X + 0.1 * rng.normal(size=X.shape)).astype(np.float32)
    return out, y


def real_activations(rows, layers, lm):
    """One teacher-forced forward per trial; capture resid_post at each candidate layer."""
    import torch
    from replay.hooks import build_input_ids, resid_post, _val
    out = {f"X_{L}": [] for L in layers}
    out.update({f"Xfirst_{L}": [] for L in layers})
    tok = lm.tokenizer
    for r in rows:
        msgs = r["messages"] + [{"role": "assistant", "content": r["text"]}]
        ids, (s, e) = build_input_ids(lm, msgs)
        saved = {}
        with lm.model.trace(torch.tensor([ids])):
            for L in layers:
                saved[L] = resid_post(lm.model.model.layers[L].output).float().save()
        for L in layers:
            h = _val(saved[L])[0].cpu().numpy()
            out[f"X_{L}"].append(h[s - 1])          # final prompt token (predicts first answer token)
            out[f"Xfirst_{L}"].append(h[min(s, len(ids) - 1)])
    return {k: np.stack(v).astype(np.float32) for k, v in out.items()}, \
        np.array([r["label"] for r in rows], dtype=np.int64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    pc = probe_cfg()
    tasks = a.tasks.split(",") if a.tasks else pc["tasks"]
    layers = [int(x) for x in pc["layer_candidates"]]
    lm = None
    if not a.mock:
        from replay.modelload import load_target
        lm = load_target("target")
    for task in tasks:
        d = Path(a.run_dir) / "probe" / task
        rows = load_trials(d)
        arrays, y = mock_activations(rows, layers) if a.mock else real_activations(rows, layers, lm)
        np.savez(d / "activations.npz", y=y, uid=np.array([r["uid"] for r in rows]),
                 param=np.array([r["param"] for r in rows], dtype=np.float64), **arrays)
        cs = Path(a.run_dir) / "features" / "model_checksum.json"
        meta = {"task": task, "layers": layers, "n": int(len(rows)), "position": pc.get("position", "prompt_final"),
                "hook_transform": "replay.hooks.resid_post (G2-verified at layer 31)", "mock": bool(a.mock),
                "model_checksum": json.loads(cs.read_text()) if cs.exists() else None}
        (d / "meta.json").write_text(json.dumps(meta, indent=2))
        print(f"[{task}] activations: n={len(rows)} layers={layers} d={arrays[f'X_{layers[0]}'].shape[1]}")


if __name__ == "__main__":
    main()
