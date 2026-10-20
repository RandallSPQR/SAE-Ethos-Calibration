"""ops on a tiny random Llama with the REAL tokenizers (ported from the trust study's p1/test_model_ops.py): cached
last-token steering equals a full forward with the same intervention, INCLUDING multi-token spellings continued from
the steered state; capture reads hidden_states[L+1]; the common spelling set is found; exact scoring with and without
the end-of-number factor equals brute force (on Llama four-digit years; the study ran this check on Gemma ages)."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
from helpers import ages, render, setup, tiny_llama, weekdays, years  # noqa: E402
from llama8b_it_base_probe import ops as mo  # noqa: E402


def _full_forward_probs(model, ids, L, v, cont=()):
    """log-probs at every position of ids + cont with layer L's output at the LAST PROMPT position set to v."""
    pos = len(ids) - 1

    def set_at(h, v=torch.tensor(v)):
        h = h.clone()
        if h.shape[1] > pos:
            h[:, pos, :] = v.to(h.dtype)
        return h
    hd = mo._layer_hook(model.model.layers[L], set_at)
    with torch.no_grad():
        lp = torch.log_softmax(model(torch.tensor([list(ids) + list(cont)])).logits[0].float(), -1)
    hd.remove()
    return lp


@pytest.mark.parametrize("family", ["llama3_base", "llama3"])
def test_cached_steering_equals_full_forward(family):
    tok, ser = setup(family)
    model = tiny_llama()
    row = ages()["rows"][100]
    ids = mo.prompt_ids(tok, ser, *render(row, family))
    V = np.random.default_rng(0).standard_normal((5, 64)).astype(np.float32)
    got = mo.Steerer(model, model.model.layers, ids).logits(28, V)
    for i in range(5):
        ref = _full_forward_probs(model, ids, 28, V[i])[-1]
        assert torch.allclose(torch.log_softmax(got[i], -1), ref, atol=1e-4)


def test_steered_multi_token_spellings_equal_brute_force():
    tok, ser = setup()
    model = tiny_llama()
    wk = weekdays()
    m, p = render(wk["rows"][3], "llama3_base")
    ids = mo.prompt_ids(tok, ser, m, p)
    ans = mo.answer_tokens(tok, ser, m, p, wk["spellings"])
    multi = mo.multi_seqs(ans)
    assert multi, "the common spelling set has multi-token forms on Llama (e.g. lowercase days)"
    V = np.random.default_rng(1).standard_normal((3, 64)).astype(np.float32)
    pl, sp = mo.Steerer(model, model.model.layers, ids).distributions(19, V, multi)
    for i in range(3):
        for k, s in list(multi.items())[:4]:
            lp = _full_forward_probs(model, ids, 19, V[i], s[:-1])
            ref = sum(float(lp[len(ids) - 1 + t, s[t]]) for t in range(len(s)))
            assert abs(np.log(sp[k][i]) - ref) < 1e-4, (k, np.log(sp[k][i]), ref)


def test_capture_reads_layer_output_and_matches_unsteered():
    tok, ser = setup()
    model = tiny_llama()
    rows = weekdays()["rows"][:6]
    ids = [mo.prompt_ids(tok, ser, *render(r, "llama3_base")) for r in rows]
    acts, probs = mo.capture(model, model.model.layers, ids, [15, 28])
    grab = {}
    hd = model.model.layers[15].register_forward_hook(lambda m, i, o: grab.__setitem__("o", o[0] if isinstance(o, tuple) else o))
    with torch.no_grad():
        model(torch.tensor([ids[2]]))
    hd.remove()
    assert np.allclose(acts[15][2], grab["o"][0, -1].numpy(), atol=1e-6)
    lg = mo.Steerer(model, model.model.layers, ids[2]).logits(28, acts[28][2][None])
    assert np.allclose(torch.softmax(lg, -1)[0].numpy(), probs[2], atol=1e-5)


def test_common_spelling_set_found_on_llama():
    tok, ser = setup()
    wk = weekdays()
    m, p = render(wk["rows"][0], "llama3_base")
    ans = mo.answer_tokens(tok, ser, m, p, wk["spellings"])
    for d, forms in ans.items():
        assert [f["spelling"] for f in forms] == [" " + d, d, " " + d.lower(), d.lower()]
    assert any(f["merged_with_cue"] for fs in ans.values() for f in fs)                          # e.g. "A:tuesday"
    ag = ages()
    m, p = render(ag["rows"][0], "llama3_base")
    ans = mo.answer_tokens(tok, ser, m, p, ag["spellings"])
    assert all(len(f["ids"]) == 1 for fs in ans.values() for f in fs) and len(ans) == 91


def test_readout_sums_variants():
    ans = {"A": [{"spelling": " A", "ids": [5]}, {"spelling": "a", "ids": [7, 8]}], "B": [{"spelling": " B", "ids": [6]}]}
    probs = np.zeros(10); probs[5], probs[6] = 0.3, 0.2
    p, mass = mo.readout(probs, {("A", "a"): 0.1}, ans, ["A", "B"])
    assert np.allclose(p, [0.4, 0.2, 0.4]) and mass["A"] == {" A": 0.3, "a": 0.1}


def test_multi_token_numbers_exact_with_end_factor():
    tok, ser = setup("llama3")
    model = tiny_llama()
    yr = years()
    m, p = render(yr["rows"][0], "llama3")
    ans = mo.answer_tokens(tok, ser, m, p, yr["spellings"])
    seqs = mo.multi_seqs(ans)
    assert len(seqs) == 21, "four-digit years are multi-token on Llama-3"
    ids = mo.prompt_ids(tok, ser, m, p)
    end = sorted({t for s in seqs.values() for t in s})
    got = mo.sequence_probs(model, ids, seqs, end)
    for key in [k for k in seqs if k[0] in (1990, 1999, 2000, 2010)]:
        s = seqs[key]
        with torch.no_grad():
            lp = torch.log_softmax(model(torch.tensor([ids + s])).logits[0].float(), -1)
        ref = np.exp(sum(float(lp[len(ids) - 1 + i, s[i]]) for i in range(len(s)))) * (1 - float(torch.exp(lp[-1, end]).sum()))
        assert abs(got[key] - ref) < 1e-6 * max(1.0, ref)
    plain = mo.sequence_probs(model, ids, {k: seqs[k] for k in list(seqs)[:3]})
    assert all(plain[k] >= got[k] for k in plain)                                                 # no factor -> no less mass
