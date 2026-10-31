"""Offline checks for the qwen3_5 serializer, CPU only:
  - it resolves through sae_ethos_pipeline's model_io entry point and satisfies the adapter interface;
  - serialize_messages == Qwen's own chat template (rendered by transformers from the identity-checked tokenizer files,
    enable_thinking=False) on a battery of conversations, as strings and as token ids;
  - the template fixture is the verified one (hash = the profiles' chat_template_hash);
  - both Qwen profiles pass modelcfg.check_tokenizer and template_agreement against the real tokenizer;
  - unsupported inputs raise.
Tests needing the tokenizer files skip without them (lineup-fetch-tokenizers)."""
import hashlib
import importlib

import pytest
import yaml

import model_io
import lineup
from lineup.model_io import FIXTURES, tokenizer_dir

TOKDIR = tokenizer_dir() / "Qwen3.5"

CONVERSATIONS = {
    "user only": [{"role": "user", "content": "Say hi."}],
    "system + user": [{"role": "system", "content": "You are a careful assistant."}, {"role": "user", "content": "Say hi."}],
    "whitespace is trimmed": [{"role": "system", "content": "  You are X.\n"}, {"role": "user", "content": "\n  padded  \n"}],
    "empty system": [{"role": "system", "content": ""}, {"role": "user", "content": "Hello"}],
    "multi-turn": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Say hi."},
                   {"role": "assistant", "content": "Hi."}, {"role": "user", "content": "Again."}],
    "assistant after last query": [{"role": "user", "content": "Say hi."}, {"role": "assistant", "content": "Hi."}],
    "assistant with think block in history": [
        {"role": "user", "content": "Q1"}, {"role": "assistant", "content": "<think>\nworking\n</think>\n\nA1"},
        {"role": "user", "content": "Q2"}],
    "assistant reasoning_content after query": [
        {"role": "user", "content": "Q"}, {"role": "assistant", "content": "A", "reasoning_content": "because"}],
    "unicode": [{"role": "user", "content": "Zoë hands him €5 — 信任? She counts the change twice."}],
    "tool results": [{"role": "user", "content": "Read it."}, {"role": "assistant", "content": "Reading."},
                     {"role": "tool", "content": "line one"}, {"role": "tool", "content": "line \"two\""}],
    "vote item shape": [{"role": "user", "content": "Your evidence: the left door is warm.\nYour current vote: B.\n"
                         "Your contacts' votes: A, B, A, A.\n\nWhich option do you vote for now? Answer with one letter: A or B."}],
}


def _tok():
    if not (TOKDIR / "tokenizer.json").exists():
        pytest.skip(f"{TOKDIR} not fetched (lineup-fetch-tokenizers)")
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(str(TOKDIR))


def test_entry_point_resolves():
    from lineup.model_io import qwen3_5
    assert model_io.get("qwen3_5") is qwen3_5
    model_io.check_adapter(qwen3_5)
    for f in ("gemma2", "gemma3", "llama3", "llama3_base", "qwen3_5"):
        model_io.check_adapter(model_io.get(f))


@pytest.mark.parametrize("name", list(CONVERSATIONS))
@pytest.mark.parametrize("gen", [True, False])
def test_serializer_matches_qwen_template(name, gen):
    tok = _tok()
    ser = model_io.get("qwen3_5")
    conv = CONVERSATIONS[name]
    theirs = tok.apply_chat_template(conv, tokenize=False, add_generation_prompt=gen, enable_thinking=False)
    ours = ser.serialize_messages(conv, add_generation_prompt=gen)
    assert ours == theirs
    ids = ser.apply_to_tokenizer(tok, conv, add_generation_prompt=gen)
    assert ids == list(tok(theirs, add_special_tokens=False)["input_ids"])
    assert tok.bos_token is None


def test_template_fixture_is_the_verified_one():
    fixture = (FIXTURES / "qwen3.5_chat_template.jinja").read_text()
    b = fixture.encode()
    assert hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest() == "a585dec894e63da457d9440ec6aa7caa16d20860"
    for n in ("qwen3.5-9b", "qwen3.5-27b"):
        assert hashlib.sha256(b).hexdigest()[:16] == yaml.safe_load(lineup.profile_path(n).read_text())[
            "target_model"]["chat_template_hash"]
    tc = TOKDIR / "tokenizer_config.json"
    if tc.exists():
        import json
        assert json.loads(tc.read_text())["chat_template"] == fixture


def test_generation_prompt_is_thinking_off():
    ser = model_io.get("qwen3_5")
    s = ser.serialize_messages(CONVERSATIONS["user only"])
    assert s == "<|im_start|>user\nSay hi.<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    assert s.endswith(ser.GENERATION_PROMPT)


@pytest.mark.parametrize("bad", [
    [{"role": "user", "content": "a"}, {"role": "system", "content": "late"}],
    [{"role": "user", "content": "a"}, {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "f"}}]}],
    [{"role": "narrator", "content": "a"}],
    [{"role": "user", "content": [{"type": "image"}]}],
    [{"role": "system", "content": "only a system message"}],
    [],
])
def test_refuses_unsupported(bad):
    with pytest.raises(ValueError):
        model_io.get("qwen3_5").serialize_messages(bad)


@pytest.mark.parametrize("name", ["qwen3.5-9b", "qwen3.5-27b"])
def test_profile_through_modelcfg(name, monkeypatch):
    tok = _tok()
    monkeypatch.setenv("MODEL_PROFILE_FILE", str(lineup.profile_path(name)))
    import modelcfg
    importlib.reload(modelcfg)
    try:
        assert modelcfg.family() == "qwen3_5" and modelcfg.profile() == name
        assert modelcfg.serializer() is model_io.get("qwen3_5")
        assert modelcfg.check_tokenizer(tok) == {"stop_token_ids": [248046], "end_of_turn_id": 248046,
                                                 "turn_suffix_ids": tok("<|im_end|>\n", add_special_tokens=False)["input_ids"]}
        agree = modelcfg.template_agreement(tok)
        assert agree["agree"] is True, agree
        assert modelcfg.replay_cfg()["layers_path"] == "model.language_model.layers"
        import calibrate.preflight_weights as pw
        assert pw.default_repos(modelcfg.models()) == [(f"Qwen/Qwen3.5-{name.split('-')[1].upper()}",
                                                        modelcfg.target()["revision"], ["*.json", "*.safetensors", "tokenizer*"])]
    finally:
        monkeypatch.delenv("MODEL_PROFILE_FILE")
        importlib.reload(modelcfg)


@pytest.mark.parametrize("name,n", [("qwen3.5-9b", 32), ("qwen3.5-27b", 64)])
def test_hooks_convention(name, n):
    prof = yaml.safe_load(lineup.profile_path(name).read_text())
    hooks = prof["hooks"]
    assert sorted(h["layer"] for h in hooks.values()) == [n // 4, n // 2, 3 * n // 4] == prof["probe"]["layer_candidates"]
    for h in hooks.values():
        L = h["layer"]
        assert h["hf_hidden_states_index"] == L + 1 and h["point"] == "resid_post"
        assert h["hf"] == f"model.model.language_model.layers[{L}].output"
