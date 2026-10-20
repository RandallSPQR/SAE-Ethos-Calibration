"""Token checks and positions on the real tokenizers (replaces the trust study's readouts/test_token_check.py, whose
cases ran the checker through the trust spec): a digit cue readout passes on base; options that merge or collide fail;
a single-token answer readout passes on Instruct while a multi-token label set fails as single-token and passes as
exact; check_readout dispatches; token_positions decodes back to the spanned words; Readout validates its fields."""
import pytest

from helpers import setup
from llama8b_it_base_probe import tokens
from llama8b_it_base_probe.spec import Readout

TEXT = "Dana is planning a picnic for Saturday. Two of the three forecasts show sun all day."
LABELS = ["not at all", "slightly", "somewhat", "moderately", "very", "extremely", "completely"]


def _base_msgs(cue="Answer: "):
    return [{"role": "user", "content": f"{TEXT}\nOn a 1-7 scale, how confident is Dana that Saturday will be sunny?\n{cue}"}]


def _chat_msgs(q):
    return [{"role": "user", "content": f"{TEXT}\n{q}"}]


def test_digit_cue_readout_passes_on_base():
    tok, ser = setup("llama3_base")
    r = tokens.check_single_token(tok, ser, _base_msgs(), [str(i) for i in range(1, 8)])
    assert r["pass"] and r["distinct"] and all(o["single_token"] for o in r["options"])
    forms = tokens.single_token_forms(tok, ser, _base_msgs(), [str(i) for i in range(1, 8)])
    assert len(forms) == 7 and all(len(f) == 1 and len(f[0]) == 1 for f in forms)


def test_merging_and_colliding_options_fail():
    tok, ser = setup("llama3_base")
    merged = tokens.check_single_token(tok, ser, _base_msgs("A:"), ["tuesday"])            # Llama merges ":t"
    assert not merged["pass"] and merged["options"][0]["tokens"] == "PREFIX CHANGED"
    assert not tokens.check_single_token(tok, ser, _base_msgs(), ["very likely", "very unlikely"])["pass"]
    first = tokens.check_single_token(tok, ser, _base_msgs("Answer:"), [" very likely", " very unlikely"], first_token=True)
    assert first["options"][0]["single_token"] and not first["distinct"] and not first["pass"]
    with pytest.raises(AssertionError):
        tokens.single_token_forms(tok, ser, _base_msgs(), ["very likely"])


def test_answer_forms_single_token_and_exact():
    tok, ser = setup("llama3")
    digits = tokens.check_answer_forms(tok, ser, _chat_msgs("Rate 1-7. Answer with the number only."),
                                       [[str(i)] for i in range(1, 8)])
    assert digits["pass"] and digits["all_single_token"] and not digits["first_token_collisions"]
    msgs = _chat_msgs(f"How sure is Dana? Answer with one of: {', '.join(LABELS)}.")
    levels = [[l, l[0].upper() + l[1:]] for l in LABELS]
    single = tokens.check_answer_forms(tok, ser, msgs, levels)
    exact = tokens.check_answer_forms(tok, ser, msgs, levels, exact=True)
    assert not single["all_single_token"] and not single["pass"]
    assert exact["pass"] and exact["scoring"].startswith("exact")


def test_check_readout_dispatches():
    tok, ser = setup("llama3_base")
    ro = Readout("expect", lambda it: _base_msgs(), options=tuple(str(i) for i in range(1, 8)))
    assert tokens.check_readout(tok, ser, ro, {})["pass"]
    tok, ser = setup("llama3")
    ro = Readout("rate", lambda it: _chat_msgs("Rate 1-7. Answer with the number only."),
                 levels=tuple((str(i),) for i in range(1, 8)))
    assert tokens.check_readout(tok, ser, ro, {})["pass"]


@pytest.mark.parametrize("family", ["llama3_base", "llama3"])
def test_token_positions_decode_to_the_spanned_words(family):
    tok, ser = setup(family)
    msgs = _base_msgs() if family == "llama3_base" else _chat_msgs("Rate 1-7. Answer with the number only.")
    a = TEXT.index("Two of the three")
    words = [(TEXT.index(w), TEXT.index(w) + len(w)) for w in ("planning", "forecasts", "sun")]
    ids, pos = tokens.token_positions(tok, ser, msgs, TEXT, {"evidence": (a, len(TEXT)), "words": words})
    assert pos["last"] == len(ids) - 1
    assert tok.decode([ids[i] for i in pos["evidence"]]).strip() == TEXT[a:]
    for (s, e), i in zip(words, pos["words"]):
        assert tok.decode([ids[i]]).strip() and tok.decode([ids[i]]).strip() in TEXT[s:e]


def test_readout_validates_fields():
    with pytest.raises(ValueError):
        Readout("x", lambda it: [], options=("1",), levels=(("1",),))
    with pytest.raises(ValueError):
        Readout("x", lambda it: [])
    with pytest.raises(ValueError):
        Readout("x", lambda it: [], options=("1", "2"), level_values=(1,))
    assert Readout("x", lambda it: [], options=("a", "b", "c")).values() == (1, 2, 3)
