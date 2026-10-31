"""Exact binary-choice readout from a lineup vLLM server (batch size 1, raw logprobs).

A readout sends the canonical serializer's TOKEN IDS (never a chat endpoint: the server's template is not used) to
/v1/completions with max_tokens 1, temperature 0 and the top 20 raw logprobs of the first generated position, keyed by
token id (return_tokens_as_token_ids). An option token outside the top 20 is read exactly by a second request with
allowed_token_ids = [that id]: under --logprobs-mode raw_logprobs the sampled token's logprob is taken BEFORE the
restriction is applied, so it is the unrestricted log-probability (the box check verifies this on an option that is
inside the top 20, where both reads must agree).

Result per call: logp per option, p per option, mass on the options, non-option mass (1 - mass), the option log-odds
logit_AB = logp[A] - logp[B], and the top-20 list for audit. What a study then does with logit_AB (decision noise,
sampling) is the study's protocol, not the pipeline's.
"""
from __future__ import annotations

import json
import math
import urllib.request

TOP = 20


def option_token_ids(tokenizer, options=("A", "B")) -> dict:
    """Each option must be ONE token when it opens the answer (no leading space: every lineup generation prompt ends
    with a newline). Raises otherwise; a study chooses option strings that pass."""
    out = {}
    for o in options:
        ids = tokenizer(o, add_special_tokens=False)["input_ids"]
        if len(ids) != 1:
            raise ValueError(f"option {o!r} is {len(ids)} tokens for this tokenizer: {ids}")
        out[o] = ids[0]
    if len(set(out.values())) != len(out):
        raise ValueError(f"options share a token: {out}")
    return out


class HTTPTransport:
    def __init__(self, base_url="http://localhost:8000/v1", api_key="x", timeout=120):
        self.base_url, self.api_key, self.timeout = base_url.rstrip("/"), api_key, timeout

    def __call__(self, payload: dict) -> dict:
        req = urllib.request.Request(self.base_url + "/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())


def _payload(model, prompt_ids, allowed=None):
    p = {"model": model, "prompt": list(prompt_ids), "max_tokens": 1, "temperature": 0.0, "logprobs": TOP,
         "seed": 0, "return_tokens_as_token_ids": True, "skip_special_tokens": False}
    if allowed is not None:
        p["allowed_token_ids"] = list(allowed)
    return p


def _tid(key):
    # vLLM renders ids as "token_id:123" when return_tokens_as_token_ids is set
    return int(str(key).split("token_id:")[-1])


def _first_position(resp):
    lp = resp["choices"][0]["logprobs"]
    top = {_tid(k): float(v) for k, v in (lp["top_logprobs"][0] or {}).items()}
    sampled = (_tid(lp["tokens"][0]), float(lp["token_logprobs"][0]))
    return top, sampled


def readout(transport, model: str, prompt_ids, option_ids: dict) -> dict:
    top, sampled = _first_position(transport(_payload(model, prompt_ids)))
    logp, how, calls = {}, {}, 1
    for o, tid in option_ids.items():
        if tid in top:
            logp[o], how[o] = top[tid], "top"
        else:
            t2, (stid, slp) = _first_position(transport(_payload(model, prompt_ids, allowed=[tid])))
            calls += 1
            if stid != tid:
                raise RuntimeError(f"restricted request for {o!r} ({tid}) sampled {stid}")
            logp[o], how[o] = slp, "restricted"
    p = {o: math.exp(v) for o, v in logp.items()}
    mass = sum(p.values())
    opts = list(option_ids)
    return {"logp": logp, "p": p, "mass": mass, "non_option_mass": max(0.0, 1.0 - mass),
            "logit_AB": logp[opts[0]] - logp[opts[1]] if len(opts) == 2 else None,
            "read_by": how, "calls": calls, "greedy_token": sampled[0],
            "top": sorted(([t, v] for t, v in top.items()), key=lambda x: -x[1])}


def repeat_probe(transport, model, prompt_ids, option_ids, n=8) -> dict:
    """n identical readouts; deterministic serving means every logp is bit-identical."""
    reads = [readout(transport, model, prompt_ids, option_ids) for _ in range(n)]
    first = reads[0]["logp"]
    identical = all(r["logp"] == first for r in reads)
    spread = {o: max(r["logp"][o] for r in reads) - min(r["logp"][o] for r in reads) for o in option_ids}
    return {"n": n, "bit_identical": identical, "max_spread_nats": spread, "logp": first}


def fallback_consistency(transport, model, prompt_ids, option_ids) -> dict:
    """For every option inside the top 20, the restricted read must equal the top-20 read (validates the fallback)."""
    top, _ = _first_position(transport(_payload(model, prompt_ids)))
    out = {}
    for o, tid in option_ids.items():
        if tid in top:
            _, (stid, slp) = _first_position(transport(_payload(model, prompt_ids, allowed=[tid])))
            out[o] = {"top": top[tid], "restricted": slp, "equal": stid == tid and slp == top[tid]}
    return {"checked": out, "ok": bool(out) and all(v["equal"] for v in out.values())}
