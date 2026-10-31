"""Serializer for the BASE model, meta-llama/Llama-3.1-8B (no chat template: base-model prompts are plain text).
Registered with sae_ethos_pipeline's model_io as family `llama3_base` (entry point in sae-ethos-pipeline's pyproject.toml).

The base model has no turns, so this adapter is deliberately narrow: an optional leading system text and ONE user text,
joined by a blank line, form a completion stem; an optional single assistant message is the continuation (for replay /
teacher forcing) and is closed by <|end_of_text|>. Anything else (several user turns, tool messages) raises, because
there is no faithful plain-text rendering of it to agree on. Nothing is trimmed or added: the item author writes the
stem, readout cue included (e.g. "...How much does A send? Answer:"), and that string is what the model sees.
"""
FAMILY = "llama3_base"
END_OF_TURN = "<|end_of_text|>"    # the base tokenizer's eos (128001); modelcfg.check_tokenizer checks it on the box
TURN_SUFFIX = END_OF_TURN
GENERATION_PROMPT = ""             # a completion stem has no opening for a model turn
SEPARATOR = "\n\n"


def user_turn(text):
    return text


def _split(messages):
    messages = list(messages)
    system = ""
    if messages and messages[0]["role"] == "system":
        system = messages[0].get("content") or ""
        messages = messages[1:]
    roles = [m["role"] for m in messages]
    if roles not in (["user"], ["user", "assistant"]):
        raise ValueError(f"base-model prompts take [system?, user, assistant?]; got roles {roles}")
    stem = (system + SEPARATOR if system else "") + (messages[0].get("content") or "")
    continuation = (messages[1].get("content") or "") if len(messages) == 2 else None
    return stem, continuation


def serialize_messages(messages, add_generation_prompt=True):
    """The stem, plus the continuation and <|end_of_text|> when an assistant message is given. add_generation_prompt
    is accepted for the interface; GENERATION_PROMPT is empty, so it changes nothing."""
    stem, continuation = _split(messages)
    return stem if continuation is None else stem + continuation + END_OF_TURN


def apply_to_tokenizer(tokenizer, messages, add_generation_prompt=True):
    """BOS (<|begin_of_text|>) + the plain text. # box-side"""
    return tokenizer(serialize_messages(messages, add_generation_prompt), add_special_tokens=True)["input_ids"]


def prompt_hash(messages):
    import hashlib
    return hashlib.sha256((FAMILY + "|" + serialize_messages(messages)).encode()).hexdigest()[:16]
