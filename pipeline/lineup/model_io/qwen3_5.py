"""The ONE canonical serializer for Qwen3.5 chat models (Qwen3.5-9B, Qwen3.5-27B), thinking OFF.

Registered with sae_ethos_pipeline's model_io as family `qwen3_5` (entry point in sae-ethos-pipeline's pyproject.toml,
since 1.2.0); generation, readout and replay reach it through modelcfg.serializer(), as they reach gemma2/gemma3/llama3.

Built by hand from the verbatim chat template of Qwen/Qwen3.5-9B @c202236235762e1c and Qwen/Qwen3.5-27B @fc05daec18b0
(byte-identical; fixtures/qwen3.5_chat_template.jinja, provenance in fixtures/PROVENANCE.md) and checked against it by
tests/test_qwen3_5.py, rendered by transformers with enable_thinking=False. What the template does, and so what this does:
  - ChatML turns: <|im_start|>{role}\\n{content|trim}<|im_end|>\\n ; no BOS (tokenizer add_bos_token false, bos None);
  - a system message is accepted only at position 0 (the template raises otherwise), content trimmed;
  - no default system message is emitted when none is given;
  - an assistant turn AFTER the last real user query is rendered with an explicit (possibly empty) reasoning block
    <think>\\n{reasoning}\\n</think>\\n\\n ; earlier assistant turns are rendered without it. Reasoning is taken from a
    `reasoning_content` string, else from a <think>...</think> prefix of the content (the template's split);
  - tool results render inside a user turn as <tool_response> blocks, consecutive ones sharing one turn; a user turn
    that is entirely a <tool_response> does not count as the last query;
  - the generation prompt, with thinking OFF (the canonical mode for StPT and Missing Piece; thinking is a later rung),
    is <|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n , so the first generated token is the answer itself.
Not supported (raise): tools / tool_calls, non-string content (images, videos), unknown roles.
"""
import hashlib

FAMILY = "qwen3_5"
END_OF_TURN = "<|im_end|>"
TURN_SUFFIX = END_OF_TURN + "\n"     # what closes a model turn in the serialized string (replay's span excludes it)
EMPTY_THINK = "<think>\n\n</think>\n\n"
GENERATION_PROMPT = "<|im_start|>assistant\n" + EMPTY_THINK
CHAT_TEMPLATE_KWARGS = {"enable_thinking": False}   # what the official template needs to render the same prompt
ROLES = ("system", "user", "assistant", "tool")


def _text(content):
    if content is None:
        return ""
    if not isinstance(content, str):
        raise ValueError("only string content is supported (no images, videos or content lists)")
    return content


def _is_tool_response(text):
    return text.startswith("<tool_response>") and text.endswith("</tool_response>")


def user_turn(text):
    """One user turn in this family's markup, without a generation prompt."""
    return "<|im_start|>user\n" + _text(text).strip() + TURN_SUFFIX


def _check(messages):
    if not messages:
        raise ValueError("no messages")
    for i, m in enumerate(messages):
        if m.get("role") not in ROLES:
            raise ValueError(f"unknown role {m.get('role')!r}")
        if m["role"] == "system" and i != 0:
            raise ValueError("a system message is accepted only at position 0")
        if m.get("tool_calls"):
            raise ValueError("assistant tool_calls are not supported by this serializer")


def _last_query_index(messages):
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m["role"] == "user" and not _is_tool_response(_text(m.get("content")).strip()):
            return i
    raise ValueError("no user query found in messages")


def serialize_messages(messages, add_generation_prompt=True):
    """The canonical prompt STRING (no BOS). Deterministic; hashed into provenance."""
    messages = list(messages)
    _check(messages)
    last_q = _last_query_index(messages)
    s = ""
    for i, m in enumerate(messages):
        role, content = m["role"], _text(m.get("content")).strip()
        if role == "system":
            s += "<|im_start|>system\n" + content + TURN_SUFFIX
        elif role == "user":
            s += "<|im_start|>user\n" + content + TURN_SUFFIX
        elif role == "assistant":
            reasoning = m.get("reasoning_content")
            if not isinstance(reasoning, str):
                reasoning = ""
                if "</think>" in content:
                    reasoning = content.split("</think>")[0].rstrip("\n").split("<think>")[-1].lstrip("\n")
                    content = content.split("</think>")[-1].lstrip("\n")
            reasoning = reasoning.strip()
            if i > last_q:
                s += "<|im_start|>assistant\n<think>\n" + reasoning + "\n</think>\n\n" + content
            else:
                s += "<|im_start|>assistant\n" + content
            s += TURN_SUFFIX
        else:  # tool
            prev = messages[i - 1]["role"] if i > 0 else None
            nxt = messages[i + 1]["role"] if i + 1 < len(messages) else None
            if prev != "tool":
                s += "<|im_start|>user"
            s += "\n<tool_response>\n" + content + "\n</tool_response>"
            if nxt != "tool":
                s += TURN_SUFFIX
    if add_generation_prompt:
        s += GENERATION_PROMPT
    return s


def apply_to_tokenizer(tokenizer, messages, add_generation_prompt=True):
    """Token IDs for generation/readout/replay: our canonical string, tokenized with no special tokens added (Qwen adds
    no BOS). One path for every consumer, instead of tokenizer.apply_chat_template."""
    return tokenizer(serialize_messages(messages, add_generation_prompt), add_special_tokens=False)["input_ids"]


def prompt_hash(messages):
    return hashlib.sha256((FAMILY + "|" + serialize_messages(messages)).encode()).hexdigest()[:16]
