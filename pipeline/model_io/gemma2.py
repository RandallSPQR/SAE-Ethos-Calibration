"""The ONE canonical serializer: semantic messages -> the exact prompt Gemma-2-9B-IT consumes.

Gemma-2's stock chat template (a) rejects a `system` role and (b) requires strictly alternating
user/assistant turns. It has no native `tool` role. So a faithful prompt must:
  - fold the system instructions into the FIRST user turn;
  - render each assistant turn as-is (model text);
  - render each tool result as the NEXT user turn ("Tool result from <tool>: ...");
  - coalesce any adjacent same-role turns so alternation holds.

Generation (target_client) AND replay (teacher-forcing) MUST both call serialize_messages(); if they
diverge, G0/G1 are meaningless. G0/G1 test THIS function. The serialized string is an instrument
artifact and its hash goes in the provenance manifest.

We build the Gemma turn structure explicitly rather than trusting an arbitrary tokenizer's template, so
the mapping is auditable. apply_to_tokenizer() wraps it with the model's special tokens on the box.
"""

GEMMA_TURN = "<start_of_turn>{role}\n{content}<end_of_turn>\n"


def to_gemma_turns(messages):
    """Return a list of (role, content) in Gemma's 'user'/'model' alternation, folding system into the
    first user turn and turning tool results into user turns. Raises on anything that can't alternate."""
    sys_text = ""
    turns = []  # (role, content) with role in {"user","model"}
    for m in messages:
        role, content = m["role"], m.get("content", "") or ""
        if role == "system":
            sys_text = (sys_text + "\n" + content).strip() if sys_text else content
            continue
        if role == "user":
            turns.append(("user", content))
        elif role == "assistant":
            turns.append(("model", content))
        elif role == "tool":
            name = m.get("name", "tool")
            turns.append(("user", f"Tool result from {name}:\n{content}"))
        else:
            raise ValueError(f"unknown role {role!r}")
    # fold system into the first user turn
    if sys_text:
        if turns and turns[0][0] == "user":
            turns[0] = ("user", sys_text + "\n\n" + turns[0][1])
        else:
            turns.insert(0, ("user", sys_text))
    # coalesce adjacent same-role turns so alternation holds (tool result after user, etc.)
    merged = []
    for role, content in turns:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n\n" + content)
        else:
            merged.append((role, content))
    # Gemma must start with a user turn
    if merged and merged[0][0] != "user":
        raise ValueError("Gemma conversation must start with a user turn after folding")
    return merged


def serialize_messages(messages, add_generation_prompt=True):
    """The canonical prompt STRING. Deterministic; hashed into provenance."""
    turns = to_gemma_turns(messages)
    s = "".join(GEMMA_TURN.format(role=r, content=c) for r, c in turns)
    if add_generation_prompt:
        s += "<start_of_turn>model\n"
    return s


def apply_to_tokenizer(tokenizer, messages, add_generation_prompt=True):
    """Token IDs for replay/generation. Uses the tokenizer's BOS + our canonical turn structure. # box-side
    Prefer this single path over tokenizer.apply_chat_template so generation and replay are byte-identical."""
    text = serialize_messages(messages, add_generation_prompt)
    return tokenizer(text, add_special_tokens=True)["input_ids"]


def prompt_hash(messages):
    import hashlib
    return hashlib.sha256(serialize_messages(messages).encode()).hexdigest()[:16]
