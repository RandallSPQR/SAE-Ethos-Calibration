"""Readouts and hidden states per item, Transformers path (bf16 by default).

  MODEL_PROFILE=llama-3.1-8b-it llama-probe-capture --spec mypkg.spec:SPEC --items ITEMS --out OUT
      [--readouts-only] [--limit N] [--dtype bfloat16]

Per item, for every readout of the active family (spec.readouts_for(ser.FAMILY)), in spec order:
  render -> forms -> score.level_probs -> {p (levels + 'other'), mass, expected}; exactly scored readouts
  (exact_end) get the end-of-label factor over score.continuation_ids.
Unless --readouts-only, also (float16, layers spec.layers; hidden_states[L + 1] = output of block L):
  from the PRIMARY readout's prompt: last (the final prompt position, where the readout is read) and, per named span
  (spec.spans): <name>_last and <name>_mean for a single span, <name> [n_spans, d] for a list of spans;
  last_<r> for every r in spec.also_last that this family reads (the primary's is a copy of `last`).
Writes OUT/readouts.jsonl, OUT/acts_L{L}.npz (rows aligned to OUT/item_ids.json), OUT/positions.json,
OUT/capture_meta.json. The layout is the trust-geometry study's capture_v2 layout, unchanged.
"""
from __future__ import annotations

import argparse
import importlib
import json
import time
from pathlib import Path

import numpy as np

from llama8b_it_base_probe import score, tokens, version


def _act_keys(spec, family, sample_spans):
    keys = ["last"]
    for name, v in sample_spans.items():
        keys += [f"{name}_last", f"{name}_mean"] if tokens._is_span(v) else [name]
    names = {r.name for r in spec.readouts_for(family)}
    keys += [f"last_{r}" for r in spec.also_last if r in names]
    return keys


def run_capture(items, spec, out, tok, model, ser, readouts_only=False, limit=0, model_id=None, log_every=200):
    """Capture on a loaded (tok, model) with the family serializer `ser`; returns the capture_meta dict."""
    import torch
    family = ser.FAMILY
    readouts = spec.readouts_for(family)
    primary = spec.primary_for(family)
    also = {r for r in spec.also_last if r in {x.name for x in readouts}}
    cont = score.continuation_ids(tok) if any(r.exact_end for r in readouts) else None
    items = items[:limit] if limit else items
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    acts, positions, t0 = None, [], time.time()
    with open(out / "readouts.jsonl", "w") as fr:
        for n, it in enumerate(items):
            rec = {"id": it["id"], **{f: it[f] for f in spec.record_fields if f in it}, "readouts": {}}
            spans = (spec.spans(it) if spec.spans else {}) if not readouts_only else {}
            if acts is None and not readouts_only:
                acts = {L: {k: [] for k in _act_keys(spec, family, spans)} for L in spec.layers}
            for ro in readouts:
                msgs = ro.messages(it)
                forms = ro.forms(tok, ser, msgs)
                ids = tok(ser.serialize_messages(msgs, add_generation_prompt=True) + ro.prefill,
                          add_special_tokens=True)["input_ids"]
                if ro.name in also and ro.name != primary and not readouts_only:
                    with torch.no_grad():
                        hs = model(torch.tensor([ids], device=model.device), output_hidden_states=True).hidden_states
                    for L in spec.layers:
                        acts[L][f"last_{ro.name}"].append(hs[L + 1][0, -1].float().cpu().numpy().astype(np.float16))
                if ro.name == primary and not readouts_only:
                    ids2, pos = tokens.token_positions(tok, ser, msgs, it["text"], spans, ro.prefill) if spans else \
                        (ids, {"last": len(ids) - 1})
                    assert ids2 == ids
                    with torch.no_grad():
                        hs = model(torch.tensor([ids], device=model.device), output_hidden_states=True).hidden_states
                    for L in spec.layers:
                        h = hs[L + 1][0].float()
                        a = acts[L]
                        a["last"].append(h[pos["last"]].cpu().numpy().astype(np.float16))
                        if primary in also:
                            a[f"last_{primary}"].append(a["last"][-1])
                        for name, v in spans.items():
                            if tokens._is_span(v):
                                a[f"{name}_last"].append(h[pos[name][-1]].cpu().numpy().astype(np.float16))
                                a[f"{name}_mean"].append(h[pos[name]].mean(0).cpu().numpy().astype(np.float16))
                            else:
                                a[name].append(h[pos[name]].cpu().numpy().astype(np.float16))
                    positions.append({"id": it["id"], **pos})
                p = score.level_probs(model, ids, forms, end_ids=cont if ro.exact_end else None)
                rec["readouts"][ro.name] = score.summarize(p, ro.values())
            fr.write(json.dumps(rec) + "\n")
            if log_every and n % log_every == 0:
                print(f"{n}/{len(items)} {time.time() - t0:.0f}s", flush=True)
    (out / "item_ids.json").write_text(json.dumps([it["id"] for it in items]))
    (out / "positions.json").write_text(json.dumps(positions))
    if not readouts_only and acts is not None:
        for L in spec.layers:
            np.savez_compressed(out / f"acts_L{L}.npz", **{k: np.stack(v) for k, v in acts[L].items()})
    meta = {"model": model_id, "family": family, "primary_readout": primary,
            "readouts": [r.name for r in readouts], "layers": list(spec.layers), "n_items": len(items),
            "readouts_only": readouts_only, "package": f"sae-ethos-pipeline {version()} llama8b_it_base_probe",
            "spec": dict(spec.meta), "seconds": round(time.time() - t0, 1)}
    (out / "capture_meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def load_spec(ref):
    """'package.module:ATTR' -> the ProjectSpec object."""
    mod, _, attr = ref.partition(":")
    return getattr(importlib.import_module(mod), attr or "SPEC")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="module:ATTR of a ProjectSpec")
    ap.add_argument("--items", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--readouts-only", action="store_true", help="no activation passes, positions or acts")
    ap.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float32"])
    a = ap.parse_args(argv)
    import modelcfg
    from llama8b_it_base_probe import load
    spec = load_spec(a.spec)
    tok, model = load.load_model(a.dtype)
    ser = modelcfg.serializer()
    tm = modelcfg.target()
    items = [json.loads(l) for l in open(a.items)]
    meta = run_capture(items, spec, a.out, tok, model, ser, a.readouts_only, a.limit, f"{tm['hf_id']}@{tm['revision']}")
    print("done", meta["n_items"], f"{meta['seconds']:.0f}s", flush=True)


if __name__ == "__main__":
    main()
