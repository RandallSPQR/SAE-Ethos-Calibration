"""The ONE canonical serializer: semantic messages -> the exact prompt Llama-3.1-8B-Instruct consumes.

Registered with sae_ethos_pipeline's model_io as family `llama3` (entry point in sae-ethos-pipeline's pyproject.toml); generation and replay
both reach it through modelcfg.serializer(), so G0/G1 test THIS function, as they test model_io.gemma2 for Gemma.

Built by hand from the verbatim chat template of meta-llama/Llama-3.1-8B-Instruct @0e9e39f249a1
(fixtures/llama3.1-8b-instruct_chat_template.jinja; provenance in fixtures/PROVENANCE.md), and checked against that
template offline by tests/test_model_io.py. What the template does, and so what this does:
  - a system header is ALWAYS emitted, even with no system message, carrying "Cutting Knowledge Date: December 2023"
    and "Today Date: <date_string>". date_string defaults to "26 Jul 2024" in the template; it is fixed here, never the
    wall clock, so a prompt is a pure function of its messages (vLLM and HF both render the default when no date is
    passed);
  - only messages[0] is lifted into that header if it is a system message; its content is trimmed;
  - every other non-tool message renders as <|start_header_id|>{role}<|end_header_id|>\\n\\n{content|trim}<|eot_id|>;
  - a `tool` message renders under the `ipython` role, its content passed through tojson (a STRING is iterable in
    Jinja, so string content is JSON-quoted; this reproduces that quirk rather than fixing it);
  - the generation prompt opens an assistant header.
Not supported (raise): tools/custom_tools/builtin_tools, assistant tool_calls, a system message after position 0
(the template would render it as a mid-conversation system turn; no single-turn readout needs one, so it is refused
rather than silently accepted). BOS is not part of the string: apply_to_tokenizer adds it with add_special_tokens=True,
and modelcfg.template_agreement strips it from the template's output before comparing.
"""
import json

FAMILY = "llama3"
END_OF_TURN = "<|eot_id|>"
TURN_SUFFIX = END_OF_TURN          # what closes a model turn in the serialized string (replay's span excludes it)
HEADER = "<|start_header_id|>{role}<|end_header_id|>\n\n"
GENERATION_PROMPT = HEADER.format(role="assistant")
DATE_STRING = "26 Jul 2024"        # the template's default date_string
SYSTEM_PREAMBLE = "Cutting Knowledge Date: December 2023\nToday Date: " + DATE_STRING + "\n\n"
ROLES = ("system", "user", "assistant", "tool")


def user_turn(text):
    """One user turn in this family's markup (no BOS, no system header, no generation prompt), per the adapter
    interface. A full prompt also needs the system header the template always emits: use serialize_messages."""
    return HEADER.format(role="user") + text.strip() + END_OF_TURN


def _tool_content(content):
    # the template: `message.content is mapping or message.content is iterable` -> tojson; strings are iterable
    if isinstance(content, (str, list, tuple, dict)):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def to_llama_turns(messages):
    """(system_text, [(header_role, rendered_content), ...]) in the template's order. Raises on anything the
    template would render in a way this study does not use."""
    messages = list(messages)
    system_text = ""
    if messages and messages[0]["role"] == "system":
        system_text = (messages[0].get("content") or "").strip()
        messages = messages[1:]
    turns = []
    for m in messages:
        role = m["role"]
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        if role == "system":
            raise ValueError("a system message is accepted only at position 0")
        if "tool_calls" in m:
            raise ValueError("assistant tool_calls are not supported by this serializer")
        if role == "tool":
            turns.append(("ipython", _tool_content(m.get("content"))))
        else:
            turns.append((role, (m.get("content") or "").strip()))
    return system_text, turns


def serialize_messages(messages, add_generation_prompt=True):
    """The canonical prompt STRING (no BOS). Deterministic; hashed into provenance."""
    system_text, turns = to_llama_turns(messages)
    s = HEADER.format(role="system") + SYSTEM_PREAMBLE + system_text + END_OF_TURN
    s += "".join(HEADER.format(role=r) + c + END_OF_TURN for r, c in turns)
    if add_generation_prompt:
        s += GENERATION_PROMPT
    return s


def apply_to_tokenizer(tokenizer, messages, add_generation_prompt=True):
    """Token IDs for replay/generation: the tokenizer's BOS + our canonical string. # box-side
    One path for both, instead of tokenizer.apply_chat_template, so generation and replay are byte-identical."""
    text = serialize_messages(messages, add_generation_prompt)
    return tokenizer(text, add_special_tokens=True)["input_ids"]


def prompt_hash(messages):
    import hashlib
    return hashlib.sha256((FAMILY + "|" + serialize_messages(messages)).encode()).hexdigest()[:16]
