#!/usr/bin/env python3
"""Activation verbalizer (the 'watcher'). Backends return the same shape, so analysis treats them
uniformly and the card's epistemic caveat (AVs may confabulate) applies to all.

Backends:
  karvonen    : the pretrained Gemma-2-9b-it activation-oracle LoRA (same model reading its own activations)
  patchscopes : zero-training self-explanation — patch the residual into a placeholder token of an
                explanation prompt at an early layer and greedy-decode. Always available; the fallback
                when the adapter is unavailable.
  harp        : retrieval agent over the activation store (not at T1)
"""
from pathlib import Path
import json
import re
import numpy as np
import yaml

CFG = Path(__file__).resolve().parent.parent / "config"

PATCH_PROMPT = "<start_of_turn>user\nDescribe in one short sentence what the text around the word \" X\" is about.<end_of_turn>\n<start_of_turn>model\nThe text is about"
HEDGES = ("unclear", "nothing", "cannot", "can't", "unknown", "empty", "no text", "not sure", "no information",
          "unable", "placeholder", "missing", "blank", "not possible", "does not", "doesn't")


class Oracle:
    def __init__(self, backend, lm, inject_layer=None, adapter=None, cfg=None):
        self.backend, self.lm, self.inject_layer, self.adapter, self.cfg = backend, lm, inject_layer, adapter, cfg or {}


def load_oracle(backend="karvonen", lm=None):
    models = yaml.safe_load((CFG / "models.yaml").read_text())
    if lm is None:
        from .modelload import load_target
        lm = load_target("target")
    if backend == "karvonen":
        oid = models["oracle"]["hf_id"]
        if not oid or oid.endswith("..."):
            raise RuntimeError("models.yaml oracle.hf_id is not filled in")
        return _load_karvonen(lm, oid, models["oracle"])
    if backend == "patchscopes":
        return Oracle("patchscopes", lm, inject_layer=int(models["oracle"].get("patch_inject_layer", 6)))
    if backend == "harp":
        raise NotImplementedError("HARP is an Arm-B backend; not part of T1")
    raise ValueError(backend)


def _hf_model(lm):
    """The underlying HF model behind the nnsight wrapper (nnsight <0.8: LanguageModel._model)."""
    for attr in ("_model", "model"):
        m = getattr(lm.model, attr, None)
        if m is not None and hasattr(m, "config") and hasattr(m, "generate"):
            return m
    raise RuntimeError("cannot find the HF model under the nnsight wrapper")


def _load_karvonen(lm, adapter_id, ocfg):
    """Karvonen activation-oracle LoRA (arXiv 2512.15674), loaded IN PLACE on the same HF weights nnsight
    wraps. PEFT swaps the Linear leaves for LoRA wrappers but leaves decoder-layer modules intact, so
    nnsight envoys stay valid; the base model is recovered with peft_model.disable_adapter(). Load this
    AFTER G0-G4 so no adapted forward can leak into those checksums.

    Format (from the repo's ao_config.json): user turn = "Layer: {L}\n" + " ?"*k + " \n" + question;
    the k placeholder tokens receive, at the OUTPUT of decoder layer `inject_layer` (=1), the norm-matched
    ADDITION h + ||h|| * v/||v|| where v is the layer-L residual at each of the k source positions."""
    from peft import PeftModel
    hf = _hf_model(lm)
    peft_model = PeftModel.from_pretrained(hf, adapter_id, revision=ocfg.get("revision") or None)
    peft_model.eval()
    return Oracle("karvonen", lm, inject_layer=int(ocfg.get("inject_layer", 1)), adapter=peft_model, cfg=ocfg)


def _karvonen_prompt(oracle, k, src_layer):
    tok = oracle.lm.tokenizer
    c = oracle.cfg
    special = c.get("special_token", " ?")
    prefix = c.get("prefix_template", "Layer: {layer}\n{placeholders} \n").format(
        layer=src_layer, placeholders=special * k)
    text = f"<start_of_turn>user\n{prefix}{c.get('question', 'What is the text about?')}<end_of_turn>\n<start_of_turn>model\n"
    ids = tok(text, add_special_tokens=True)["input_ids"]
    sid = tok(special, add_special_tokens=False)["input_ids"][-1]
    pos = [i for i, t in enumerate(ids) if t == sid]
    if len(pos) < k:
        raise RuntimeError(f"found {len(pos)} placeholder tokens, expected {k}: {tok.convert_ids_to_tokens(ids)}")
    return ids, pos[-k:]


def _explain_karvonen(oracle, vecs, max_new_tokens=24, src_layer=None):
    """vecs: [k, d_model] residuals (layer src_layer) for k consecutive positions -> explanation string."""
    import torch
    lm, tok = oracle.lm, oracle.lm.tokenizer
    src_layer = src_layer if src_layer is not None else lm.layer
    vecs = np.asarray(vecs, dtype=np.float32)
    if vecs.ndim == 1:
        vecs = vecs[None]
    k = vecs.shape[0]
    ids, pos = _karvonen_prompt(oracle, k, src_layer)
    v = torch.as_tensor(vecs)
    hf = oracle.adapter
    base = hf.get_base_model()
    layer = base.model.layers[oracle.inject_layer]
    injected = {"done": False}

    def hook(mod, args, out):
        if injected["done"]:
            return out
        h = out[0] if isinstance(out, tuple) else out
        if h.shape[1] < max(pos) + 1:
            return out                      # KV-cached decode step; only the prefill carries placeholders
        h = h.clone()
        vv = v.to(h.device, h.dtype)
        norms = vv.norm(dim=-1, keepdim=True)
        unit = torch.where(norms > 0, vv / norms.clamp_min(1e-6), torch.zeros_like(vv))
        hn = h[0, pos].float().norm(dim=-1, keepdim=True).to(h.dtype)
        h[0, pos] = h[0, pos] + hn * unit * float(oracle.cfg.get("steering_coefficient", 1.0))
        injected["done"] = True
        return (h,) + tuple(out[1:]) if isinstance(out, tuple) else h

    hdl = layer.register_forward_hook(hook)
    try:
        with torch.no_grad():
            out = hf.generate(torch.tensor([ids]).to(base.device), max_new_tokens=max_new_tokens,
                              do_sample=False, eos_token_id=[1, 107], pad_token_id=0)
    finally:
        hdl.remove()
    gen = out[0, len(ids):].tolist()
    gen = [t for t in gen if t not in (0, 1, 107)]
    return tok.decode(gen).strip()


# ---------------------------------------------------------------- patchscopes backend
def _patch_ids(tok):
    ids = tok(PATCH_PROMPT, add_special_tokens=True)["input_ids"]
    x_id = tok(" X", add_special_tokens=False)["input_ids"][-1]
    pos = max(i for i, t in enumerate(ids) if t == x_id)
    return ids, pos


def _explain_patchscopes(oracle, vec, max_new_tokens=16):
    import torch
    from .hooks import _val, resid_post, _set_block_output
    lm, tok = oracle.lm, oracle.lm.tokenizer
    ids, pos = _patch_ids(tok)
    v = torch.as_tensor(np.asarray(vec, dtype=np.float32))
    layer = lm.model.model.layers[oracle.inject_layer]
    out = []
    cur = list(ids)
    for _ in range(max_new_tokens):
        with lm.model.trace(torch.tensor([cur])):
            stream = resid_post(layer.output)
            stream[0, pos] = v.to(stream.device, stream.dtype)
            _set_block_output(layer, stream)
            nxt = lm.model.output.logits[0, -1].argmax().save()
        t = int(_val(nxt))
        if t in (1, 107):
            break
        out.append(t)
        cur.append(t)
    return tok.decode(out).strip()


def _window(oracle, residual, p):
    w = int(oracle.cfg.get("window", 8)) if oracle.backend == "karvonen" else 1
    return residual[max(1, p - w + 1):p + 1]          # never include BOS (pos 0)


def _explain(oracle, vecs):
    if oracle.backend == "karvonen":
        return _explain_karvonen(oracle, vecs)
    if oracle.backend == "patchscopes":
        return _explain_patchscopes(oracle, vecs[-1] if np.asarray(vecs).ndim == 2 else vecs)
    raise NotImplementedError(oracle.backend)


def verbalize(oracle, residual, positions):
    """[{'position','explanation','source'}] for the given positions (karvonen: a window ending at p)."""
    return [{"position": int(p), "explanation": _explain(oracle, _window(oracle, residual, p)),
             "source": oracle.backend} for p in positions]


def is_confident_specific(text):
    t = text.lower().strip()
    return len(t.split()) >= 3 and not any(h in t for h in HEDGES)


def confabulation_probe(oracle, residual, positions, rng=None):
    """Zeroed and shuffled activations; rate of confident, specific explanations."""
    rng = rng or np.random.default_rng(0)
    d = residual.shape[-1]
    outs = []
    for p in positions:
        w = _window(oracle, residual, p)
        outs.append(("zero", _explain(oracle, np.zeros_like(w))))
        outs.append(("shuffled", _explain(oracle, np.stack([rng.permutation(r) for r in w]))))
    rate = sum(is_confident_specific(t) for _, t in outs) / max(1, len(outs))
    return rate, outs


CODE_WORDS = ("code", "program", "python", "function", "software", "algorithm", "fibonacci", "prime", "comput", "variable", "script", "number")
PROSE_WORDS = ("river", "flood", "water", "farm", "rain", "children", "village", "story", "weather", "bridge", "nature", "school")


def gather_residuals(lm, tok, texts, n_per=6, seed=0):
    """{label: (residual[seq,d], positions)} captured through the UNADAPTED replay path. Call BEFORE
    load_oracle('karvonen') so the LoRA can never leak into these activations."""
    from .hooks import teacher_forced_forward
    rng = np.random.default_rng(seed)
    out = {}
    for label, text in texts.items():
        ids = tok(text, add_special_tokens=True)["input_ids"]
        fr = teacher_forced_forward(lm, None, capture_residual=True, input_ids=ids)
        pos = sorted(rng.choice(np.arange(8, len(ids)), size=min(n_per, len(ids) - 8), replace=False).tolist())
        out[label] = (fr.residual, pos)
    return out


def oracle_calibration_report(oracle, samples, out_path):
    """Accuracy on labeled activations (code vs prose positions, keyword-scored explanations) and
    confabulation rate on null activations. samples from gather_residuals. Writes oracle_calibration.json."""
    rng = np.random.default_rng(0)
    rows, correct = [], 0
    for label, words in (("code", CODE_WORDS), ("prose", PROSE_WORDS)):
        resid, pos = samples[label]
        exps = verbalize(oracle, resid, pos)
        for e in exps:
            hit = any(w in e["explanation"].lower() for w in words)
            correct += hit
            rows.append({"label": label, **e, "hit": hit})
    acc = correct / max(1, len(rows))
    confab, null_outs = confabulation_probe(oracle, samples["code"][0], samples["code"][1][:3], rng)
    rep = {"backend": oracle.backend, "accuracy": acc, "confab_rate": confab, "n": len(rows),
           "explanations": rows, "null_explanations": [{"kind": k, "text": t, "confident": is_confident_specific(t)} for k, t in null_outs]}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(rep, indent=2))
    return rep
