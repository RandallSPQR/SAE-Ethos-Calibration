"""Token-level checks and positions, CPU only (tokenizer, no model).

  single_token_forms   [[ids]] per option for a cue readout; asserts each option (or, first_token, its first word) adds
                       exactly one token after the prompt and nothing merges across the boundary
  check_single_token   the same as a report: per option single_token + tokens, and whether the options' tokens are
                       distinct (a cue readout passes only if every option is one token and all are distinct)
  check_answer_forms   an answer readout (spellings per level after the generation prompt, nothing prefilled): every
                       form must preserve the prompt's tokens; a single-token readout (exact=False) also needs every
                       required form to be one token and no first token shared across levels; an exactly scored readout
                       (exact=True, multi-token with the end factor) records token counts and first-token collisions
  check_readout        dispatch on a spec.Readout for one item
  token_positions      char spans inside the item text -> token indices in the fully serialized prompt

These checks are where a project's readout validity starts, per tokenizer; passing them is necessary, not sufficient.
"""
from __future__ import annotations


def _prompt(ser, messages, prefill=""):
    return ser.serialize_messages(messages, add_generation_prompt=True) + prefill


def _probe(opt, first_token):
    if not first_token:
        return opt
    return " " + opt.split()[0] if opt.startswith(" ") else opt.split()[0]


def single_token_forms(tok, ser, messages, options, first_token=False, prefill=""):
    """[[ids]] per option (one form per level), each exactly one token at the boundary; raises otherwise."""
    s = _prompt(ser, messages, prefill)
    base = tok(s, add_special_tokens=True)["input_ids"]
    forms = []
    for opt in options:
        ids = tok(s + _probe(opt, first_token), add_special_tokens=True)["input_ids"]
        if not (ids[:len(base)] == base and len(ids) == len(base) + 1):
            raise AssertionError(f"option {opt!r} is not a single token at the boundary: "
                                 f"{tok.convert_ids_to_tokens(ids[len(base) - 2:])}")
        forms.append([ids[len(base):]])
    return forms


def check_single_token(tok, ser, messages, options, first_token=False, prefill=""):
    s = _prompt(ser, messages, prefill)
    base = tok(s, add_special_tokens=True)["input_ids"]
    rows, firsts = [], []
    for opt in options:
        ids = tok(s + _probe(opt, first_token), add_special_tokens=True)["input_ids"]
        prefix = ids[:len(base)] == base
        ok = prefix and len(ids) == len(base) + 1
        firsts.append(ids[len(base)] if ok else None)
        rows.append({"option": opt, "single_token": bool(ok),
                     "tokens": tok.convert_ids_to_tokens(ids[len(base):]) if prefix else "PREFIX CHANGED"})
    distinct = None not in firsts and len(set(firsts)) == len(firsts)
    return {"pass": all(r["single_token"] for r in rows) and distinct, "distinct": distinct, "options": rows,
            "prompt_tokens": len(base)}


def check_answer_forms(tok, ser, messages, levels, exact=False, prefill=""):
    """levels: [[required spellings] per level]."""
    s = _prompt(ser, messages, prefill)
    base = tok(s, add_special_tokens=True)["input_ids"]
    out, owner, collide = [], {}, []
    for li, spellings in enumerate(levels):
        forms = []
        for f in spellings:
            ids = tok(s + f, add_special_tokens=True)["input_ids"]
            prefix = ids[:len(base)] == base
            n = len(ids) - len(base) if prefix else None
            forms.append({"form": f, "prefix_ok": prefix, "n_tokens": n,
                          "tokens": tok.convert_ids_to_tokens(ids[len(base):]) if prefix else "PREFIX CHANGED"})
            if prefix and n:
                first = ids[len(base)]
                if first in owner and owner[first] != li:
                    collide.append((f, owner[first], li))
                owner.setdefault(first, li)
        out.append(forms)
    single = all(f["n_tokens"] == 1 for lv in out for f in lv)
    ok = all(f["prefix_ok"] for lv in out for f in lv) and (exact or (single and not collide))
    return {"pass": bool(ok), "scoring": "exact multi-token with end factor" if exact else "single token",
            "all_single_token": single, "first_token_collisions": collide, "levels": out}


def check_readout(tok, ser, readout, item):
    """The check that fits a spec.Readout, on one item."""
    msgs = readout.messages(item)
    if readout.options:
        return check_single_token(tok, ser, msgs, readout.options, readout.first_token, readout.prefill)
    return check_answer_forms(tok, ser, msgs, readout.levels, readout.exact_end, readout.prefill)


def _is_span(v):
    return len(v) == 2 and all(isinstance(x, int) for x in v)


def token_positions(tok, ser, messages, text, spans, prefill=""):
    """-> (ids, {"last": index of the final prompt token, name: [...]}). spans {name: (start, end)} gives every token
    overlapping that char span of `text`; {name: [(start, end), ...]} gives the FIRST token of each span. Offsets are
    relative to `text`, which must occur verbatim in the serialized prompt (its first occurrence is used)."""
    s = _prompt(ser, messages, prefill)
    enc = tok(s, add_special_tokens=True, return_offsets_mapping=True)
    off = s.index(text)
    om = enc["offset_mapping"]

    def toks(a, b):
        return [j for j, (x, y) in enumerate(om) if y > x and x < off + b and y > off + a]
    pos = {"last": len(enc["input_ids"]) - 1}
    for name, v in (spans or {}).items():
        pos[name] = toks(*v) if _is_span(v) else [toks(a, b)[0] for a, b in v]
    return enc["input_ids"], pos
