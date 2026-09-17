#!/usr/bin/env python3
"""Teacher-forcing + activation capture + steering injection.

These three operations share one forward pass abstraction so G1 (fidelity), replay (capture), and
G4/steer (injection) all go through the same code path — if they diverged, a fidelity pass could
validate a path the real run doesn't use.
"""
from dataclasses import dataclass
import numpy as np


@dataclass
class ForwardResult:
    token_ids: list          # input ids (teacher-forced)
    logits_argmax: list      # argmax at each position i (predicts token_ids[i+1])
    input_logprobs: list     # input_logprobs[i] = logprob that position i-1 assigns to token_ids[i]; [0]=0.0
    residual: "np.ndarray"   # [seq, d_model] at the VERIFIED hook_resid_post (float32)
    assistant_span: tuple    # [start, end) of the final model turn's CONTENT tokens (no end_of_turn)
    extra: dict = None       # optional: other captured tensors {name: np.ndarray}
    logits_top2: list = None # [[id1, id2], ...] per position (G1 flip excuse: is the sampled token in replay's top-2?)


TURN_SUFFIX = "<end_of_turn>\n"


def build_input_ids(lm, messages, upto=None):
    """Token ids via the ONE canonical serializer (model_io.gemma2.apply_to_tokenizer) — the SAME
    function generation uses. Returns (ids, assistant_span) where the span covers the final model turn's
    content tokens: prefix = everything before it serialized WITH the generation prompt, so
    span = [len(prefix_ids), len(full_ids) - len(suffix_ids)). Asserts the prefix tokenizes identically
    inside the full sequence (a boundary-merge would silently shift every activation)."""
    from model_io.gemma2 import apply_to_tokenizer
    msgs = list(messages if upto is None else messages[:upto])
    tok = lm.tokenizer
    full = apply_to_tokenizer(tok, msgs, add_generation_prompt=False)
    if not msgs or msgs[-1]["role"] != "assistant":
        return full, (len(full), len(full))
    prefix = apply_to_tokenizer(tok, msgs[:-1], add_generation_prompt=True)
    if full[:len(prefix)] != prefix:
        raise RuntimeError("prefix ids are not a prefix of the full ids: tokenization boundary drift")
    n_suffix = len(tok(TURN_SUFFIX, add_special_tokens=False)["input_ids"])
    return full, (len(prefix), len(full) - n_suffix)


def _as_tuple(out):
    return out if isinstance(out, (tuple, list)) else (out,)


def resid_post(layer_output):
    """The residual stream LEAVING a Gemma-2 decoder block, from the block's output under nnsight/HF.

    In transformers' Gemma2DecoderLayer the block returns `(hidden_states, [attn_weights])` (or a bare
    tensor in newer versions) where hidden_states = residual + post_feedforward_layernorm(mlp) — i.e. the
    FULL stream. vLLM's Gemma2DecoderLayer instead returns (hidden_states, residual) split, whose sum is
    the stream. This function handles both: a 2-tuple whose parts share the residual shape is summed;
    otherwise element 0 is the stream. G2 is the empirical check that this equals SAELens
    'blocks.31.hook_resid_post' (the canonical SAE must reproduce Google's FVU/L0 here and at no decoy)."""
    parts = _as_tuple(layer_output)
    if len(parts) >= 2 and hasattr(parts[1], "shape") and tuple(parts[1].shape) == tuple(parts[0].shape):
        return parts[0] + parts[1]
    return parts[0]


def _set_block_output(layer, new_stream):
    """Write a modified stream back as the block's output (steering)."""
    out = layer.output
    if isinstance(out, (tuple, list)):
        layer.output = (new_stream,) + tuple(out[1:])
    else:
        layer.output = new_stream


def _val(x):
    """Unwrap an nnsight saved proxy (0.4: .value; 0.5+: the tensor itself) and detach it."""
    v = getattr(x, "value", x)
    return v.detach() if hasattr(v, "detach") else v


def teacher_forced_forward(lm, messages, capture_residual=True, steer=None, input_ids=None,
                           extra_hooks=None, span=None):
    """One forward over the teacher-forced sequence. Captures the residual via resid_post() at the
    verified hook. If steer=(vector[d_model], strength), adds strength * mean_residual_norm * unit(vector)
    at every position before it flows onward. Also returns per-position logprobs of the input ids.
    input_ids overrides serialization (used for raw-id diagnostics). extra_hooks: list of reader names
    from modelload.HOOK_READERS to capture alongside (G2 decoys)."""
    import torch
    from .modelload import residual_module
    if input_ids is None:
        input_ids, span = build_input_ids(lm, messages)
    elif span is None:
        span = (len(input_ids), len(input_ids))
    model = lm.model
    layer = residual_module(lm)
    ids_t = torch.tensor([list(input_ids)])
    saved = {}
    # nnsight requires envoys to be touched in EXECUTION order: submodule outputs (input, attn, mlp,
    # post-ffn norm) must be read before the block's own output.
    order = {"block_input": 0, "attn": 1, "mlp": 2, "post_ffn_norm": 3, "block_output": 4}
    extras = sorted([h for h in (extra_hooks or []) if h != "block_output"], key=lambda h: order.get(h, 9))
    with torch.no_grad(), model.trace(ids_t):
        for name in extras:
            saved[name] = _read_hook(layer, name).float().save()
        stream = resid_post(layer.output)
        if steer is not None:
            vec, strength = steer
            v = torch.as_tensor(np.asarray(vec, dtype=np.float32)).to(stream.device, stream.dtype)
            unit = v / (v.norm() + 1e-6)
            mean_norm = stream[0, 1:].float().norm(dim=-1).mean().to(stream.dtype)   # exclude BOS
            stream = stream + float(strength) * mean_norm * unit
            _set_block_output(layer, stream)
        if capture_residual:
            saved["resid"] = stream.float().save()
        saved["logits"] = model.output.logits.float().save()
    logits = _val(saved["logits"])[0]                                      # [seq, vocab]
    lp = torch.log_softmax(logits, dim=-1)
    ids = list(input_ids)
    argmax = logits.argmax(-1).tolist()
    top2 = logits.topk(2, dim=-1).indices.tolist()
    input_logprobs = [0.0] + [float(lp[i - 1, ids[i]]) for i in range(1, len(ids))]
    resid = _val(saved["resid"])[0].cpu().numpy() if capture_residual else None
    extra = {k: _val(v)[0].cpu().numpy() for k, v in saved.items() if k not in ("resid", "logits")}
    return ForwardResult(token_ids=ids, logits_argmax=argmax, input_logprobs=input_logprobs,
                         residual=resid, assistant_span=tuple(span), extra=extra, logits_top2=top2)


def _read_hook(layer, reader):
    """Read a decoy tensor off the block during a trace (see modelload.HOOK_READERS)."""
    if reader == "block_output":
        return resid_post(layer.output)
    if reader == "block_input":
        inp = layer.input
        # nnsight versions differ: .input may be the first positional arg or ((args),{kwargs})
        if isinstance(inp, (tuple, list)):
            first = inp[0]
            if isinstance(first, (tuple, list)):
                first = first[0]
            return first
        return inp
    if reader == "mlp":
        return _as_tuple(layer.mlp.output)[0]
    if reader == "attn":
        return _as_tuple(layer.self_attn.output)[0]
    if reader == "post_ffn_norm":
        return _as_tuple(layer.post_feedforward_layernorm.output)[0]
    raise KeyError(reader)


def greedy_generate(lm, prompt_ids, max_new_tokens=32, eos_ids=(1, 107)):
    """Greedy decoding THROUGH teacher_forced_forward (the observation path), so G0b compares vLLM
    against exactly the computation replay uses. Returns generated ids (EOS excluded)."""
    ids = list(prompt_ids)
    out = []
    for _ in range(max_new_tokens):
        fr = teacher_forced_forward(lm, None, capture_residual=False, input_ids=ids)
        nxt = fr.logits_argmax[-1]
        if nxt in eos_ids:
            break
        out.append(nxt)
        ids.append(nxt)
    return out


def sampled_ids_from_transcript(row):
    """The ids the target actually emitted for the scored assistant turn (from generation).
    Populated by replay from row['tokens']['sampled_ids']; used only by G1."""
    return (row.get("tokens") or {}).get("sampled_ids")


def greedy_generate_at_layer(lm, prompt_ids, layer, steer, max_new_tokens=8, eos_ids=(1, 107)):
    """Greedy decoding with activation addition at an ARBITRARY block index (the probe track's layer may
    differ from the SAE hook layer). Same transform as teacher_forced_forward(steer=...): h' = h +
    strength * mean_resid_norm * unit(vector), applied at every position of block `layer`. Additive helper;
    existing signatures unchanged."""
    import torch
    vec, strength = steer
    v = torch.as_tensor(np.asarray(vec, dtype=np.float32))
    block = lm.model.model.layers[int(layer)]
    ids = list(prompt_ids)
    out = []
    for _ in range(max_new_tokens):
        with lm.model.trace(torch.tensor([ids])):
            stream = resid_post(block.output)
            unit = (v / (v.norm() + 1e-6)).to(stream.device, stream.dtype)
            mean_norm = stream[0, 1:].float().norm(dim=-1).mean().to(stream.dtype)
            _set_block_output(block, stream + float(strength) * mean_norm * unit)
            nxt = lm.model.output.logits[0, -1].argmax().save()
        t = int(_val(nxt))
        if t in eos_ids:
            break
        out.append(t)
        ids.append(t)
    return out


def sample_generate_at_layer(lm, prompt_ids, layer, steer, temperature=0.8, top_p=0.95, seed=0,
                             max_new_tokens=6, eos_ids=(1, 107)):
    """Sampled decoding (temperature + nucleus, seeded) with activation addition at block `layer`, same
    transform as greedy_generate_at_layer. steer=None or strength 0 -> plain sampling. Used by the probe
    track's steered psychometric sweeps, where a graded curve needs T>0 across agents."""
    import torch
    gen = torch.Generator(device="cpu").manual_seed(int(seed))
    vec, strength = (steer if steer is not None else (None, 0.0))
    v = None if vec is None else torch.as_tensor(np.asarray(vec, dtype=np.float32))
    block = lm.model.model.layers[int(layer)]
    ids = list(prompt_ids)
    out = []
    for _ in range(max_new_tokens):
        with torch.no_grad(), lm.model.trace(torch.tensor([ids])):
            if v is not None and float(strength) != 0.0:
                stream = resid_post(block.output)
                unit = (v / (v.norm() + 1e-6)).to(stream.device, stream.dtype)
                mean_norm = stream[0, 1:].float().norm(dim=-1).mean().to(stream.dtype)
                _set_block_output(block, stream + float(strength) * mean_norm * unit)
            logits = lm.model.output.logits[0, -1].float().save()
        lg = _val(logits).cpu()
        if temperature <= 0:
            t = int(lg.argmax())
        else:
            p = torch.softmax(lg / temperature, dim=-1)
            sp, si = torch.sort(p, descending=True)
            keep = (torch.cumsum(sp, 0) - sp) < top_p
            sp = sp * keep
            t = int(si[torch.multinomial(sp / sp.sum(), 1, generator=gen)])
        if t in eos_ids:
            break
        out.append(t)
        ids.append(t)
    return out


def _left_pad(tok, prompt_ids_list):
    """Left-pad a list of id lists. Returns input_ids [B,T], attention_mask [B,T], position_ids [B,T]
    (positions counted over REAL tokens only, so a padded row sees the same positions as unbatched)."""
    import torch
    pad = tok.pad_token_id if tok.pad_token_id is not None else 0
    T = max(len(x) for x in prompt_ids_list)
    ids = torch.full((len(prompt_ids_list), T), pad, dtype=torch.long)
    mask = torch.zeros((len(prompt_ids_list), T), dtype=torch.long)
    for i, x in enumerate(prompt_ids_list):
        ids[i, T - len(x):] = torch.tensor(x); mask[i, T - len(x):] = 1
    pos = (mask.cumsum(-1) - 1).clamp(min=0)
    return ids, mask, pos


def last_logits_batch(lm, prompt_ids_list, layer=None, steer=None):
    """Last-position logits [B, V] (float32) for a batch of prompts, left-padded, with optional activation
    addition at block `layer` on the real tokens of every row. Same transform as the unbatched path; the
    batch gate (probe.batch_gate) proves equality to the G1 tolerance before this is trusted."""
    import torch
    ids, mask, pos = _left_pad(lm.tokenizer, prompt_ids_list)
    block = lm.model.model.layers[int(layer)] if layer is not None else None
    with torch.no_grad(), lm.model.trace({"input_ids": ids, "attention_mask": mask, "position_ids": pos}):
        if steer is not None and float(steer[1]) != 0.0 and block is not None:
            stream = resid_post(block.output)
            v = torch.as_tensor(np.asarray(steer[0], dtype=np.float32))
            unit = (v / (v.norm() + 1e-6)).to(stream.device, stream.dtype)
            m = mask.to(stream.device).bool()
            # per-row mean residual norm over real tokens excluding each row's first real token (BOS)
            first = (mask.cumsum(-1) == 1).to(stream.device)
            use = m & ~first
            norms = stream.float().norm(dim=-1)
            mean_norm = ((norms * use).sum(-1) / use.sum(-1).clamp(min=1)).to(stream.dtype)      # [B]
            add = (float(steer[1]) * mean_norm)[:, None, None] * unit[None, None, :] * m[:, :, None].to(stream.dtype)
            _set_block_output(block, stream + add)
        logits = lm.model.output.logits[:, -1].float().save()
    return _val(logits)                      # stays on the GPU; sampling happens there


def sample_from_logits(lg, temperature, top_p, seed):
    """Seeded temperature + nucleus sampling on whatever device `lg` lives on (GPU in the batched path)."""
    import torch
    if temperature <= 0:
        return int(lg.argmax())
    gen = torch.Generator(device=lg.device).manual_seed(int(seed))
    p = torch.softmax(lg / temperature, dim=-1)
    sp, si = torch.sort(p, descending=True)
    keep = (torch.cumsum(sp, 0) - sp) < top_p
    sp = sp * keep
    return int(si[torch.multinomial(sp / sp.sum(), 1, generator=gen)])


def sample_generate_batch_at_layer(lm, prompt_ids_list, layer, steer, temperature=0.8, top_p=0.95, seeds=None,
                                   max_new_tokens=6, eos_ids=(1, 107)):
    """Batched counterpart of sample_generate_at_layer: all rows advance one token per forward; a row stops
    at EOS. Per-row seeded sampling. Returns list of generated id lists."""
    seeds = seeds or list(range(len(prompt_ids_list)))
    cur = [list(x) for x in prompt_ids_list]
    outs = [[] for _ in cur]; done = [False] * len(cur)
    for step in range(max_new_tokens):
        live = [i for i in range(len(cur)) if not done[i]]
        if not live:
            break
        lg = last_logits_batch(lm, [cur[i] for i in live], layer, steer)
        for j, i in enumerate(live):
            t = sample_from_logits(lg[j], temperature, top_p, seeds[i] * 104729 + step)
            if t in eos_ids:
                done[i] = True; continue
            outs[i].append(t); cur[i].append(t)
    return outs
