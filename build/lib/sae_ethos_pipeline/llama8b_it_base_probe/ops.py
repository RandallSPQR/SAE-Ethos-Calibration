"""Model operations on the Transformers path (plain AutoModelForCausalLM, output_hidden_states; no nnsight, no vLLM).
This is a different forward path from sae_ethos_pipeline's replay/hooks.py (nnsight): a project that makes replay-
fidelity claims on it validates it against served generation itself.

  prompt_ids        serializer + prefill, tokenized with BOS, exactly as the readout reads it
  answer_tokens     per value, EVERY spelling of a common set that continues the cue cleanly (prefix-preserving),
                    with its token ids: single tokens and multi-token forms alike (checked on a real prompt)
  capture           last-token activations at the requested layers (hidden_states[L + 1] = output of layer L) and the
                    last-position probabilities, batched by prompt length (no padding: Llama's position ids ignore the mask)
  sequence_probs    chain probability of each multi-token spelling after the prompt; with `end_tokens` (spellings that
                    prefix one another, e.g. multi-token numbers) times the probability the spelling ENDS there,
                    1 - mass on any end token, so "10" is not counted inside "100" nor "47" inside "475"
  Steerer           last-token intervention at layer L for a batch of replacement vectors: the prompt minus its last
                    token runs once and its KV cache is copied per batch; the last token runs with the layer's output at
                    that position replaced; multi-token spellings then continue from the STEERED state (hook off),
                    one batched pass per spelling length. No end_tokens factor on this path.
  readout           value probabilities = sum over the value's spellings (single + multi), the per-spelling masses,
                    and 'other' = 1 - sum
"""
from __future__ import annotations

import copy
from collections import defaultdict

import numpy as np


def prompt_ids(tok, ser, messages, prefill):
    s = ser.serialize_messages(messages, add_generation_prompt=True) + prefill
    return tok(s, add_special_tokens=True)["input_ids"]


def answer_tokens(tok, ser, messages, prefill, spellings):
    """value -> [{"spelling", "ids", "merged_with_cue"}] for EVERY spelling (the same set on every model).
    ids = the spelling's canonical continuation when tokenizing prompt + spelling preserves the prompt's tokens; when the
    spelling merges with the cue (e.g. Llama ":t" in "A:tuesday"), the spelling tokenized on its own, appended after the
    prompt, and flagged."""
    s = ser.serialize_messages(messages, add_generation_prompt=True) + prefill
    base = tok(s, add_special_tokens=True)["input_ids"]
    out = {}
    for v, sps in spellings.items():
        forms = []
        for sp in sps:
            ids = tok(s + sp, add_special_tokens=True)["input_ids"]
            if ids[:len(base)] == base and len(ids) > len(base):
                forms.append({"spelling": sp, "ids": ids[len(base):], "merged_with_cue": False})
            else:
                own = tok(sp, add_special_tokens=False)["input_ids"]
                if not own:
                    raise ValueError(f"spelling {sp!r} tokenizes to nothing")
                forms.append({"spelling": sp, "ids": own, "merged_with_cue": True})
        out[v] = forms
    return out


def multi_seqs(ans):
    """{(value, spelling): ids} for the multi-token forms."""
    return {(v, f["spelling"]): f["ids"] for v, fs in ans.items() for f in fs if len(f["ids"]) > 1}


def readout(probs_last, seqp, ans, values):
    """probs_last [vocab]; seqp {(value, spelling): prob} -> (vector over values + 'other', {value: {spelling: mass}})."""
    mass = {}
    for v in values:
        mass[v] = {f["spelling"]: float(probs_last[f["ids"][0]]) if len(f["ids"]) == 1 else float(seqp[(v, f["spelling"])])
                   for f in ans[v]}
    p = np.array([sum(mass[v].values()) for v in values], dtype=np.float64)
    return np.append(p, max(0.0, 1.0 - p.sum())), mass


def _layer_hook(layer, fn):
    def hook(mod, inp, out):
        if isinstance(out, tuple):
            return (fn(out[0]), *out[1:])
        return fn(out)
    return layer.register_forward_hook(hook)


def capture(model, layers_mod, ids_list, layers, batch=32):
    """-> ({L: [N, d] float32}, [N, vocab] float32 last-token probs) in input order. Batches by equal length only."""
    import torch
    by_len = defaultdict(list)
    for i, ids in enumerate(ids_list):
        by_len[len(ids)].append(i)
    acts = {L: [None] * len(ids_list) for L in layers}
    probs = [None] * len(ids_list)
    with torch.no_grad():
        for n, idx in by_len.items():
            for s in range(0, len(idx), batch):
                chunk = idx[s:s + batch]
                x = torch.tensor([ids_list[i] for i in chunk], device=model.device)
                out = model(x, output_hidden_states=True)
                hs = out.hidden_states
                p = torch.softmax(out.logits[:, -1].float(), dim=-1).cpu().numpy()
                for j, i in enumerate(chunk):
                    probs[i] = p[j]
                    for L in layers:
                        acts[L][i] = hs[L + 1][j, -1].float().cpu().numpy()
    return {L: np.stack(v) for L, v in acts.items()}, np.stack(probs)


def sequence_probs(model, prompt, seqs, end_tokens=None):
    """prompt [ids]; seqs {key: [token ids]} -> {key: probability} (see module doc)."""
    import torch
    with torch.no_grad():
        out = model(torch.tensor([prompt], device=model.device), use_cache=True)
    root = torch.log_softmax(out.logits[0, -1].float(), -1)
    cache0 = out.past_key_values
    need = sorted({tuple(s[:i]) for s in seqs.values() for i in range(1, len(s) + (1 if end_tokens else 0))})
    logp = {(): root}
    by_len = defaultdict(list)
    for n in need:
        by_len[len(n)].append(n)
    with torch.no_grad():
        for L, group in by_len.items():
            c = copy.deepcopy(cache0)
            c.batch_repeat_interleave(len(group))
            o = model(torch.tensor([list(g) for g in group], device=model.device), past_key_values=c, use_cache=True)
            lp = torch.log_softmax(o.logits[:, -1].float(), -1)
            for j, g in enumerate(group):
                logp[g] = lp[j]
    res = {}
    for k, s in seqs.items():
        lp = sum(float(logp[tuple(s[:i])][s[i]]) for i in range(len(s)))
        end = 1.0
        if end_tokens:
            end = 1.0 - float(torch.exp(logp[tuple(s)][list(end_tokens)]).sum())
        res[k] = float(np.exp(lp) * max(end, 0.0))
    return res


class Steerer:
    """Last-token steering at one layer for one prompt, reusing the prompt-prefix KV cache."""

    def __init__(self, model, layers_mod, ids):
        import torch
        self.model, self.layers_mod, self.ids = model, layers_mod, ids
        with torch.no_grad():
            out = model(torch.tensor([ids[:-1]], device=model.device), use_cache=True)
        self.cache = out.past_key_values

    def _steered(self, layer, vectors):
        import torch
        V = torch.as_tensor(np.asarray(vectors), device=self.model.device)
        n = V.shape[0]
        cache = copy.deepcopy(self.cache)
        cache.batch_repeat_interleave(n)

        def set_last(h):
            h = h.clone()
            h[:, -1, :] = V.to(h.dtype)
            return h
        handle = _layer_hook(self.layers_mod[layer], set_last)
        try:
            with torch.no_grad():
                out = self.model(torch.tensor([[self.ids[-1]]] * n, device=self.model.device), past_key_values=cache,
                                 use_cache=True)
        finally:
            handle.remove()
        return out.logits[:, -1].float(), out.past_key_values

    def logits(self, layer, vectors):
        """vectors [n, d] -> last-position logits [n, vocab] float32 with layer `layer`'s output at the last token
        replaced by each vector."""
        return self._steered(layer, vectors)[0]

    def distributions(self, layer, vectors, multi):
        """-> (probs_last [n, vocab] float64 numpy, {key: [n] chain probability}) for the multi-token spellings `multi`
        ({key: ids}), continued from the steered state with the hook OFF."""
        import torch
        logits, cache = self._steered(layer, vectors)
        lp_last = torch.log_softmax(logits, -1)
        n = lp_last.shape[0]
        out = {}
        groups = defaultdict(list)
        for k, ids in multi.items():
            groups[len(ids)].append(k)
        for L, keys in groups.items():
            m = len(keys)
            T = torch.tensor([multi[k] for k in keys], device=self.model.device)            # [m, L]
            c = copy.deepcopy(cache)
            c.batch_repeat_interleave(m)                                                      # row = i_n * m + j
            with torch.no_grad():
                o = self.model(T[:, :-1].repeat(n, 1), past_key_values=c, use_cache=False)
            lp = torch.log_softmax(o.logits.float(), -1).view(n, m, L - 1, -1)
            for j, k in enumerate(keys):
                s = lp_last[:, multi[k][0]].clone()
                for t in range(1, L):
                    s += lp[:, j, t - 1, multi[k][t]]
                out[k] = torch.exp(s).cpu().numpy().astype(np.float64)
        return torch.exp(lp_last).cpu().numpy().astype(np.float64), out
