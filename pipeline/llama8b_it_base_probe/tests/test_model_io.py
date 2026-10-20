"""Offline checks for the Llama adapters, CPU only (ported from the trust study's model_io/test_llama3.py):
  - both adapters resolve through sae_ethos_pipeline's model_io entry points and satisfy its interface;
  - llama3.serialize_messages == Meta's own chat template (rendered by transformers from the OID-verified
    tokenizer_config.json) on a battery of conversations, as strings and as token ids, with exactly one BOS;
  - modelcfg, pointed at the shipped profiles by MODEL_PROFILE_FILE, passes check_tokenizer and template_agreement
    against the real tokenizers;
  - the hard-coded hooks keep the resid_post(i) == hidden_states[i + 1] convention and the three layers.
Tests needing the tokenizer files skip without them."""
import hashlib
import json

import pytest
import yaml

import sae_ethos_pipeline  # noqa: F401  (activates the pipeline's import root)
import model_io

from helpers import BASE, INSTRUCT, TOK, reset_profile, tok as _tok, use_profile
from llama8b_it_base_probe import profile_path
from llama8b_it_base_probe.model_io import FIXTURES

IT_PROFILE = profile_path("llama-3.1-8b-it")
BASE_PROFILE = profile_path("llama-3.1-8b")

CONVERSATIONS = {
    "user only": [{"role": "user", "content": "Say hi."}],
    "system + user": [{"role": "system", "content": "You are a careful assistant."},
                      {"role": "user", "content": "Say hi."}],
    "whitespace is trimmed": [{"role": "system", "content": "  You are X.\n"},
                              {"role": "user", "content": "\n  padded  \n"}],
    "empty system": [{"role": "system", "content": ""}, {"role": "user", "content": "Hello"}],
    "multi-turn": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Say hi."},
                   {"role": "assistant", "content": "Hi."}, {"role": "user", "content": "Again."}],
    "unicode": [{"role": "user", "content": "Zoë hands him €5 — 信任? She counts the change twice."}],
    "tool string content": [{"role": "user", "content": "Read it."}, {"role": "assistant", "content": "Reading."},
                            {"role": "tool", "content": "line one\nline \"two\""}],
    "tool dict content": [{"role": "user", "content": "Look up."}, {"role": "assistant", "content": "Ok."},
                          {"role": "tool", "content": {"amount": 10, "note": "tripled"}}],
    "readout item shape": [{"role": "user", "content": "The forecast shows rain all afternoon."
                                                       "\n\nOn a 1-5 scale, how likely is it to stay dry? Answer with one number."}],
}


def _ids(x):
    return list(x["input_ids"] if hasattr(x, "keys") else x)


@pytest.fixture
def profile(monkeypatch):
    yield lambda path: use_profile(monkeypatch, path)
    reset_profile(monkeypatch)


def test_entry_points_resolve_and_builtins_unchanged():
    from llama8b_it_base_probe.model_io import llama3, llama3_base
    assert model_io.get("llama3") is llama3 and model_io.get("llama3_base") is llama3_base
    for f in ("gemma2", "gemma3", "llama3", "llama3_base"):
        model_io.check_adapter(model_io.get(f))


@pytest.mark.parametrize("name", list(CONVERSATIONS))
@pytest.mark.parametrize("gen", [True, False])
def test_serializer_matches_meta_template(name, gen):
    tok = _tok(INSTRUCT)
    ser = model_io.get("llama3")
    conv = CONVERSATIONS[name]
    theirs = tok.apply_chat_template(conv, tokenize=False, add_generation_prompt=gen)
    assert theirs.startswith(tok.bos_token)
    assert ser.serialize_messages(conv, add_generation_prompt=gen) == theirs[len(tok.bos_token):]
    ids = ser.apply_to_tokenizer(tok, conv, add_generation_prompt=gen)
    assert ids == _ids(tok(theirs, add_special_tokens=False))
    assert ids[0] == tok.bos_token_id == 128000 and ids.count(128000) == 1


def test_template_fixture_is_the_verified_one():
    cfg = TOK / INSTRUCT / "tokenizer_config.json"
    t = json.loads(cfg.read_text())["chat_template"] if cfg.exists() else None
    fixture = (FIXTURES / "llama3.1-8b-instruct_chat_template.jinja").read_text()
    assert hashlib.sha256(fixture.encode()).hexdigest()[:16] == yaml.safe_load(IT_PROFILE.read_text())[
        "target_model"]["chat_template_hash"]
    if t is not None:
        assert t == fixture


def test_generation_prompt_and_suffix():
    ser = model_io.get("llama3")
    s = ser.serialize_messages(CONVERSATIONS["user only"])
    assert s.endswith(ser.GENERATION_PROMPT)
    assert s.startswith("<|start_header_id|>system<|end_header_id|>\n\nCutting Knowledge Date: December 2023\n"
                        "Today Date: 26 Jul 2024\n\n<|eot_id|>")
    assert ser.prompt_hash(CONVERSATIONS["user only"]) != model_io.get("llama3_base").prompt_hash(CONVERSATIONS["user only"])


@pytest.mark.parametrize("bad", [
    [{"role": "user", "content": "a"}, {"role": "system", "content": "late"}],
    [{"role": "user", "content": "a"}, {"role": "assistant", "content": "", "tool_calls": [{}]}],
    [{"role": "narrator", "content": "a"}],
])
def test_llama3_refuses_unsupported(bad):
    with pytest.raises(ValueError):
        model_io.get("llama3").serialize_messages(bad)


def test_instruct_profile_through_modelcfg(profile):
    tok = _tok(INSTRUCT)
    modelcfg = profile(IT_PROFILE)
    assert modelcfg.family() == "llama3" and modelcfg.profile() == "llama-3.1-8b-it"
    assert modelcfg.serializer() is model_io.get("llama3")
    assert modelcfg.check_tokenizer(tok) == {"stop_token_ids": [128009], "end_of_turn_id": 128009,
                                             "turn_suffix_ids": [128009]}
    agree = modelcfg.template_agreement(tok)
    assert agree["agree"] is True, agree
    assert modelcfg.sae_layer() == 19
    assert modelcfg.hook_names()["blocks.19.hook_resid_post"] == "block_output"


def test_base_profile_through_modelcfg(profile):
    tok = _tok(BASE)
    modelcfg = profile(BASE_PROFILE)
    assert modelcfg.family() == "llama3_base" and modelcfg.serializer() is model_io.get("llama3_base")
    assert modelcfg.check_tokenizer(tok)["stop_token_ids"] == [128001]
    assert getattr(tok, "chat_template", None) is None
    ser = modelcfg.serializer()
    stem = [{"role": "user", "content": "Alice is 7 years old. Bob is 5 years older than Alice. Q: How old is Bob?\nA: Bob is "}]
    ids = ser.apply_to_tokenizer(tok, stem)
    assert ids[0] == 128000 and ids.count(128000) == 1
    assert tok.decode(ids[1:]) == stem[0]["content"]          # nothing added, nothing trimmed (trailing space kept)
    with_cont = stem + [{"role": "assistant", "content": "12"}]
    assert ser.serialize_messages(with_cont).endswith("Bob is 12<|end_of_text|>")
    with pytest.raises(ValueError):
        ser.serialize_messages(stem + [{"role": "user", "content": "again"}])


@pytest.mark.parametrize("path", [IT_PROFILE, BASE_PROFILE])
def test_hooks_hard_coded_convention(path):
    hooks = yaml.safe_load(path.read_text())["hooks"]
    assert {h["layer"] for h in hooks.values()} == {15, 19, 28}
    for h in hooks.values():
        L = h["layer"]
        assert h["point"] == "resid_post" and h["hf_hidden_states_index"] == L + 1
        assert h["hf"] == f"model.model.layers[{L}].output"
        assert h["vllm"] == f"model.model.layers[{L}].output[0] + output[1]"


@pytest.mark.parametrize("path", [IT_PROFILE, BASE_PROFILE])
def test_profiles_keep_the_study_pins(path):
    """The extracted profiles change no pinned value: revision and weight hash as pinned on the 2026-10-10 pod."""
    t = yaml.safe_load(path.read_text())["target_model"]
    pins = {"llama-3.1-8b-it": ("0e9e39f249a16976918f6564b8830bc894c89659", "495b73750141622d"),
            "llama-3.1-8b": ("d04e592bb4f6aa9cfee91e2e20afa771667e1d4b", "ac5be7e65a45c454")}
    assert (t["revision"], t["weight_hash"]) == pins[path.stem.removeprefix("models_")]
    assert "glp" not in yaml.safe_load(path.read_text())
