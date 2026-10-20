"""Shared test helpers: the real tokenizers (skip without them), a tiny random Llama with the real vocabulary, the
P1 control-task prompts (weekdays, ages; Wurgaft et al. 2026, used here only as test inputs), and a profile switcher."""
import importlib

import pytest

from llama8b_it_base_probe import profile_path
from llama8b_it_base_probe.model_io import tokenizer_dir

TOK = tokenizer_dir()
BASE, INSTRUCT = "Llama-3.1-8B", "Llama-3.1-8B-Instruct"
FAMILY_TOK = {"llama3_base": BASE, "llama3": INSTRUCT}
FAMILY_PROFILE = {"llama3_base": profile_path("llama-3.1-8b"), "llama3": profile_path("llama-3.1-8b-it")}


def tok(name):
    d = TOK / name
    if not (d / "tokenizer.json").exists():
        pytest.skip(f"{d} not fetched (llama-probe-fetch-tokenizers)")
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(str(d))


def setup(family="llama3_base"):
    import sae_ethos_pipeline  # noqa: F401
    import model_io
    return tok(FAMILY_TOK[family]), model_io.get(family)


def tiny_llama(n_layers=30):
    import torch
    from transformers import LlamaConfig, LlamaForCausalLM
    torch.manual_seed(0)
    cfg = LlamaConfig(vocab_size=128256, hidden_size=64, intermediate_size=128, num_hidden_layers=n_layers,
                      num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=512, bos_token_id=128000,
                      eos_token_id=128001, attn_implementation="eager")
    return LlamaForCausalLM(cfg).eval()


def use_profile(monkeypatch, family_or_path):
    path = FAMILY_PROFILE.get(family_or_path, family_or_path)
    monkeypatch.setenv("MODEL_PROFILE_FILE", str(path))
    import sae_ethos_pipeline  # noqa: F401
    import modelcfg
    return importlib.reload(modelcfg)


def reset_profile(monkeypatch):
    monkeypatch.delenv("MODEL_PROFILE_FILE", raising=False)
    import modelcfg
    importlib.reload(modelcfg)


# ---- control-task prompts (test inputs only) ---------------------------------------------------------------------------
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NUM_WORDS = ["one", "two", "three", "four", "five", "six", "seven"]


def weekdays():
    rows = [{"question": f"Q: What day is {w} days after {day}?", "cue": "A:", "value": DAYS[(e + k) % 7]}
            for e, day in enumerate(DAYS) for k, w in enumerate(NUM_WORDS, start=1)]
    return {"rows": rows, "spellings": {d: [" " + d, d, " " + d.lower(), d.lower()] for d in DAYS}}


def ages():
    rows = [{"question": f"Alice is {e} years old. Bob is {k} years older than Alice. Q: How old is Bob?",
             "cue": "A: Bob is ", "value": e + k} for e in range(1, 100) for k in range(1, 11) if 10 <= e + k <= 100]
    return {"rows": rows, "spellings": {v: [str(v)] for v in sorted({r["value"] for r in rows})}}


def years():
    """Four-digit years: Llama-3 splits them into multi-token digit groups whose spellings prefix one another, the
    case the end-of-number factor exists for."""
    rows = [{"question": f"Ann was born in {y}. Q: In which year was Ann born?", "cue": "A: ", "value": y}
            for y in range(1990, 2011)]
    return {"rows": rows, "spellings": {r["value"]: [str(r["value"])] for r in rows}}


def render(row, family):
    """(messages, prefill): the base model reads question + cue as plain text; chat models get the cue prefilled."""
    if family == "llama3_base":
        return [{"role": "user", "content": f"{row['question']}\n{row['cue']}"}], ""
    return [{"role": "user", "content": row["question"]}], row["cue"]
