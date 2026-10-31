"""The readout against a fake vLLM completions endpoint: top-20 reads, the exact restricted fallback, non-option mass,
the repeat probe and the fallback consistency check."""
import math

import pytest

from lineup import readout


class FakeServer:
    """Raw log-probs over a toy vocabulary; honours allowed_token_ids the way vLLM's raw_logprobs mode does (the
    sampled token's logprob is the UNRESTRICTED one). `jitter` perturbs repeated calls to emulate nondeterminism."""
    def __init__(self, logp, jitter=0.0):
        self.logp, self.jitter, self.calls = dict(logp), jitter, []

    def __call__(self, payload):
        self.calls.append(payload)
        lp = {t: v + (self.jitter * len(self.calls) if self.jitter else 0.0) for t, v in self.logp.items()}
        top = dict(sorted(lp.items(), key=lambda kv: -kv[1])[:payload["logprobs"]])
        allowed = payload.get("allowed_token_ids")
        tok = allowed[0] if allowed else max(lp, key=lp.get)
        return {"choices": [{"logprobs": {"tokens": [f"token_id:{tok}"], "token_logprobs": [lp[tok]],
                                          "top_logprobs": [{f"token_id:{t}": v for t, v in top.items()}]}}]}


def vocab(pA, pB, n_other=40):
    rest = (1 - pA - pB) / n_other
    d = {1: math.log(pA), 2: math.log(pB)}
    d.update({100 + i: math.log(rest) for i in range(n_other)})
    return d


def test_top_read():
    srv = FakeServer(vocab(0.7, 0.25))
    r = readout.readout(srv, "m", [5, 6], {"A": 1, "B": 2})
    assert r["calls"] == 1 and r["read_by"] == {"A": "top", "B": "top"}
    assert r["p"]["A"] == pytest.approx(0.7) and r["non_option_mass"] == pytest.approx(0.05)
    assert r["logit_AB"] == pytest.approx(math.log(0.7 / 0.25))
    assert srv.calls[0]["prompt"] == [5, 6] and srv.calls[0]["max_tokens"] == 1 and srv.calls[0]["temperature"] == 0.0


def test_restricted_fallback_is_exact():
    d = vocab(0.9, 0.0005, n_other=30)
    for i in range(30):                      # 30 other tokens, each above B: B falls outside the top 20
        d[100 + i] = math.log((1 - 0.9 - 0.0005) / 30)
    srv = FakeServer(d)
    r = readout.readout(srv, "m", [1], {"A": 1, "B": 2})
    assert r["read_by"] == {"A": "top", "B": "restricted"} and r["calls"] == 2
    assert r["logp"]["B"] == pytest.approx(math.log(0.0005))
    assert srv.calls[1]["allowed_token_ids"] == [2]


def test_repeat_probe_detects_nondeterminism():
    assert readout.repeat_probe(FakeServer(vocab(0.6, 0.3)), "m", [1], {"A": 1, "B": 2}, n=5)["bit_identical"]
    r = readout.repeat_probe(FakeServer(vocab(0.6, 0.3), jitter=1e-6), "m", [1], {"A": 1, "B": 2}, n=5)
    assert not r["bit_identical"] and r["max_spread_nats"]["A"] > 0


def test_fallback_consistency():
    assert readout.fallback_consistency(FakeServer(vocab(0.6, 0.3)), "m", [1], {"A": 1, "B": 2})["ok"]


def test_option_tokens_must_be_single():
    class Tok:
        def __call__(self, s, add_special_tokens=False):
            return {"input_ids": {"A": [32], "B": [33], "AB": [32, 33]}[s]}
    assert readout.option_token_ids(Tok()) == {"A": 32, "B": 33}
    with pytest.raises(ValueError):
        readout.option_token_ids(Tok(), ("A", "AB"))
