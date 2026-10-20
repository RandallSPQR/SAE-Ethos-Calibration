"""Exact scoring (ported from the trust study's readouts/test_score.py), small: tiny random Llama, the real Instruct
tokenizer, an answer readout with a 7-label intensity scale (some labels split into several tokens at the start of a
turn). Checks: every label form tokenizes after the prompt; the exact score equals a brute-force chain product with the
end-of-word factor; the end factor only removes mass; levels sum their case variants and 'other' closes the
distribution; single-token readouts score from one pass; summarize renormalizes the expected value."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
from helpers import setup, tiny_llama  # noqa: E402
from llama8b_it_base_probe import score  # noqa: E402
from llama8b_it_base_probe.spec import Readout  # noqa: E402

ITEM = "Maya and Theo are neighbors. Maya hands Theo the spare key without a second thought."
LABELS = ["not at all", "slightly", "somewhat", "moderately", "very", "extremely", "completely"]
WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _render(question):
    return lambda it: [{"role": "user", "content": f"{it['text']}\n{question}"}]


VERBAL = Readout("verbal", _render(f"How much does Maya like Theo? Answer with one of: {', '.join(LABELS)}. "
                                   "Answer with the label only."),
                 levels=tuple((l, l[0].upper() + l[1:]) for l in LABELS), exact_end=True)
SINGLE = {
    "rating": Readout("rating", _render("On a 1-7 scale, how much does Maya like Theo? Answer with the number only."),
                      levels=tuple((str(i),) for i in range(1, 8))),
    "words": Readout("words", _render("Maya has $10. How many dollars does Maya lend Theo? Answer with the number word only."),
                     levels=tuple((w, w.capitalize()) for w in WORDS), level_values=tuple(range(11))),
}


def _prompt(tok, ser, ro):
    msgs = ro.messages({"text": ITEM})
    return msgs, tok(ser.serialize_messages(msgs, add_generation_prompt=True), add_special_tokens=True)["input_ids"]


def test_verbal_forms_cover_every_level():
    tok, ser = setup("llama3")
    msgs, _ = _prompt(tok, ser, VERBAL)
    forms = VERBAL.forms(tok, ser, msgs)
    assert len(forms) == 7 and all(len(fs) == 2 for fs in forms)
    assert any(len(f) > 1 for fs in forms for f in fs), "some labels must be multi-token (the reason for exact scoring)"
    for li, fs in enumerate(forms):
        for f in fs:
            assert tok.decode(f).strip().lower() == LABELS[li]


def test_continuation_ids_classify_word_pieces():
    tok, _ = setup("llama3")
    cont = set(score.continuation_ids(tok))
    ly = tok("ly", add_special_tokens=False)["input_ids"]
    sp = tok(" very", add_special_tokens=False)["input_ids"]
    assert ly[0] in cont and sp[0] not in cont and tok.convert_tokens_to_ids(".") not in cont


def test_exact_score_equals_brute_force_with_end_factor():
    tok, ser = setup("llama3")
    model = tiny_llama()
    msgs, pids = _prompt(tok, ser, VERBAL)
    forms = VERBAL.forms(tok, ser, msgs)
    cont = score.continuation_ids(tok)
    got = score.level_probs(model, pids, forms, end_ids=cont)
    assert got.shape == (8,) and np.isclose(got.sum(), 1.0) and (got >= 0).all()
    with torch.no_grad():
        for li in (1, 6):
            ref = 0.0
            for f in forms[li]:
                lp = torch.log_softmax(model(torch.tensor([pids + f])).logits[0].float(), -1)
                chain = sum(float(lp[len(pids) - 1 + i, f[i]]) for i in range(len(f)))
                end = 1.0 - float(torch.exp(lp[len(pids) - 1 + len(f), cont]).sum())
                ref += np.exp(chain) * end
            assert np.isclose(got[li], ref, rtol=1e-4, atol=1e-9), (li, got[li], ref)
    no_end = score.level_probs(model, pids, forms, end_ids=None)
    assert (got[:7] <= no_end[:7] + 1e-12).all()


@pytest.mark.parametrize("name", list(SINGLE))
def test_single_token_readouts_score_without_end_factor(name):
    tok, ser = setup("llama3")
    model = tiny_llama()
    ro = SINGLE[name]
    msgs, pids = _prompt(tok, ser, ro)
    forms = ro.forms(tok, ser, msgs)
    assert all(len(f) == 1 for fs in forms for f in fs)
    p = score.level_probs(model, pids, forms)
    with torch.no_grad():
        q = torch.softmax(model(torch.tensor([pids])).logits[0, -1].float(), -1)
    assert np.isclose(p[0], sum(float(q[f[0]]) for f in forms[0]), rtol=1e-5)


def test_summarize_renormalizes_expected_value():
    r = score.summarize([0.1, 0.3, 0.4, 0.2], (1, 2, 3))
    assert r["mass"] == pytest.approx(0.8) and r["expected"] == pytest.approx((0.1 + 0.6 + 1.2) / 0.8)
    assert score.summarize([0, 0, 1.0], (0, 1))["mass"] == 0.0
