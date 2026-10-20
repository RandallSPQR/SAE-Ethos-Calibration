"""Exact scoring of a readout's levels at the answer position.

A level is a set of forms (token-id sequences). Each form is scored exactly:
  P(form) = prod_i p(t_i | prompt, t_<i)  x  P(end | prompt, form)        (the end factor only when end_ids is given)
  P(end)  = 1 - mass on WORD-CONTINUATION tokens after the form (tokens whose text starts with a letter or a digit,
            i.e. would extend the last word: "very" + "ly", "slight" + "ly").
A level's probability is the sum over its forms (e.g. lower- and upper-case first letter); 'other' = 1 - sum.
Without end_ids, single-token forms are read from one forward pass and multi-token forms by their chain probability
(ops.sequence_probs).
"""
from __future__ import annotations

import re

import numpy as np

from llama8b_it_base_probe import ops

_CONT = {}


def continuation_ids(tok) -> list[int]:
    """Token ids whose decoded text starts with a letter or digit (no leading space or punctuation)."""
    key = getattr(tok, "name_or_path", id(tok))
    if key not in _CONT:
        ids = []
        for i in range(len(tok)):
            s = tok.decode([i])
            if s and re.match(r"[A-Za-z0-9]", s):
                ids.append(i)
        _CONT[key] = ids
    return _CONT[key]


def level_probs(model, prompt_ids, forms, end_ids=None):
    """forms [[ids of each form] per level] -> [L + 1] probabilities (levels, then 'other'). With end_ids, every form
    (single- or multi-token) gets the end-of-word factor."""
    seqs = {(li, fi): f for li, fs in enumerate(forms) for fi, f in enumerate(fs)}
    if end_ids is None:
        import torch
        with torch.no_grad():
            p = torch.softmax(model(torch.tensor([prompt_ids], device=model.device)).logits[0, -1].float(), -1)
        multi = {k: v for k, v in seqs.items() if len(v) > 1}
        sp = ops.sequence_probs(model, prompt_ids, multi) if multi else {}
        pr = {k: (float(p[v[0]]) if len(v) == 1 else sp[k]) for k, v in seqs.items()}
    else:
        pr = ops.sequence_probs(model, prompt_ids, seqs, end_tokens=end_ids)
    out = np.array([sum(pr[(li, fi)] for fi in range(len(fs))) for li, fs in enumerate(forms)], dtype=np.float64)
    return np.append(out, max(0.0, 1.0 - out.sum()))


def summarize(p, level_values):
    """The record capture writes per readout: the distribution, the readout mass (1 - 'other') and the expected level
    value renormalized over the levels."""
    p = np.asarray(p, dtype=np.float64)
    mass = float(1 - p[-1])
    return {"p": [float(x) for x in p], "mass": mass,
            "expected": float((p[:-1] * np.asarray(level_values)).sum() / max(mass, 1e-12))}
