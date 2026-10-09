"""Serializer for Gemma-3 IT (27B parameterization, 2026-09-29).

Gemma-3 IT uses the Gemma-2 turn markup (<start_of_turn>user|model ... <end_of_turn>), and its chat template folds a
system message into the first user turn rather than giving it a role, which is what the Gemma-2 serializer already
does by hand. So the turn logic is shared (plus the template's per-message trim), and this module exists so the family is explicit in the profile, in the
serializer hash and in the manifest. Token ids are NOT assumed equal to Gemma-2's (the vocabularies differ): the
profile states stop_token_ids and modelcfg.check_tokenizer verifies them against the tokenizer on the box, and
modelcfg.template_agreement compares this serializer's output with the tokenizer's own template (recorded, read at G0).
"""
from model_io.gemma2 import to_gemma_turns as _gemma2_turns, GEMMA_TURN, GENERATION_PROMPT, user_turn   # noqa: F401

END_OF_TURN = "<end_of_turn>"
TURN_SUFFIX = END_OF_TURN + "\n"
FAMILY = "gemma3"


def to_gemma_turns(messages):
    """Gemma-2's folding (system into the first user turn, tool results as user turns, adjacent same-role turns
    coalesced), with each message's content TRIMMED first, as Gemma-3's chat template does (`content | trim`; the
    system text is not trimmed there and is not here). Verified offline against the template's verbatim text
    (model_io/fixtures/gemma3_chat_template.jinja, from google/gemma-3-27b-it @005ad340) by model_io/test_gemma3.py."""
    trimmed = []
    for m in messages:
        m = dict(m)
        if m.get("role") != "system":
            m["content"] = (m.get("content") or "").strip()
        trimmed.append(m)
    return _gemma2_turns(trimmed)


def serialize_messages(messages, add_generation_prompt=True):
    turns = to_gemma_turns(messages)
    s = "".join(GEMMA_TURN.format(role=r, content=c) for r, c in turns)
    if add_generation_prompt:
        s += "<start_of_turn>model\n"
    return s


def apply_to_tokenizer(tokenizer, messages, add_generation_prompt=True):
    text = serialize_messages(messages, add_generation_prompt)
    return tokenizer(text, add_special_tokens=True)["input_ids"]


def prompt_hash(messages):
    import hashlib
    return hashlib.sha256((FAMILY + "|" + serialize_messages(messages)).encode()).hexdigest()[:16]
