"""The one reader of the model config (27B parameterization, 2026-09-29).

MODEL_PROFILE selects config/models_<profile>.yaml; unset, config/models.yaml (Gemma-2-9B-IT, the closed 9B study)
is read, so every 9B path, hash and manifest is unchanged. Everything that used to be a Gemma-2-9B constant in code
(the serializer module, stop token ids, the turn suffix, the decoder-layer path, hook names keyed on layer 31, the
replay dtype/device map/attention, Neuronpedia ids) is read here from the profile, and the parts that a tokenizer
or a loaded model can contradict are CHECKED against them on the box (check_tokenizer, decoder_layers), never trusted.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

import yaml

CFG = Path(__file__).resolve().parent / "config"

# Gemma-2-9B values for keys the 9B models.yaml predates; a new profile states every one of them explicitly.
_LEGACY = {"family": "gemma2", "stop_token_ids": [107, 1], "layers_path": "model.layers",
           "neuronpedia": {"model_id": "gemma-2-9b-it", "source": "31-gemmascope-res-16k"}}


def profile() -> str | None:
    return os.environ.get("MODEL_PROFILE") or None


def models_path() -> Path:
    p = profile()
    return CFG / (f"models_{p}.yaml" if p else "models.yaml")


@lru_cache(maxsize=None)
def _load(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())


def sae_role() -> str:
    """SAE_ROLE=secondary makes every consumer see the profile's `sae_secondary` block as `sae` (its layer, hooks, decoys,
    Neuronpedia ids, calibration anchors), so the single-SAE ladder (G2 decoys, identity, instrument checks, analysis) runs
    unchanged for the pre-registered secondary layer. Unset = primary. The bulk feature store for the secondary is
    captured in the primary replay's own forward pass (replay.replay), not by a second replay."""
    r = os.environ.get("SAE_ROLE") or "primary"
    if r not in ("primary", "secondary"):
        raise ValueError(f"SAE_ROLE {r!r}")
    return r


@lru_cache(maxsize=None)
def _resolved(path: str, role: str) -> dict:
    base = _load(path)
    if role == "primary":
        return base
    sec = base.get("sae_secondary")
    if not sec:
        raise RuntimeError(f"SAE_ROLE=secondary but {Path(path).name} has no sae_secondary block")
    return {**base, "sae": sec, "neuronpedia": sec.get("neuronpedia", base.get("neuronpedia")),
            "calibration": {**(base.get("calibration") or {}), **(sec.get("calibration") or {})}, "sae_role": "secondary"}


def models() -> dict:
    f = models_path()
    if not f.exists():
        raise FileNotFoundError(f"MODEL_PROFILE={profile()!r} names {f.name}, which does not exist")
    return _resolved(str(f), sae_role())


def secondary_sae() -> dict | None:
    """The pre-registered secondary SAE block (captured alongside the primary), or None."""
    return None if sae_role() == "secondary" else _load(str(models_path())).get("sae_secondary")


def models_hash() -> str:
    return hashlib.sha256(models_path().read_bytes()).hexdigest()[:16]


def target() -> dict:
    return models()["target_model"]


def family() -> str:
    return models().get("family", _LEGACY["family"])


def serializer():
    """The model family's serializer module (model_io.<family>): serialize_messages, apply_to_tokenizer,
    prompt_hash, TURN_SUFFIX, END_OF_TURN. Generation and replay both call this; G0/G1 test it."""
    import model_io
    return model_io.get(family())


def stop_token_ids() -> list[int]:
    return list(target().get("stop_token_ids", _LEGACY["stop_token_ids"]))


def turn_suffix() -> str:
    return serializer().TURN_SUFFIX


def replay_cfg() -> dict:
    """dtype (float32: rules 2026-09-29.1), device_map ('cuda' one card; 'auto' splits a model over several),
    attn_implementation (eager: Gemma soft-capping is exact only under eager)."""
    r = dict(models().get("replay") or {})
    r.setdefault("dtype", "float32")
    r.setdefault("device_map", "cuda")
    r.setdefault("attn_implementation", "eager")
    r.setdefault("layers_path", models().get("layers_path", _LEGACY["layers_path"]))
    return r


def serve_cfg() -> dict:
    """What the driver passes to `vllm serve` (read by calibrate/model_env.sh)."""
    tm = target()
    s = dict(models().get("serve") or {})
    s.setdefault("model", tm["hf_id"])
    s.setdefault("served_model_name", models()["endpoint"]["served_model_name"])
    s.setdefault("revision", tm.get("revision"))
    s.setdefault("dtype", "float32")
    s.setdefault("max_model_len", 8192)
    s.setdefault("tensor_parallel_size", 1)
    s.setdefault("extra_args", "")
    return s


def neuronpedia() -> dict:
    return dict(models().get("neuronpedia") or _LEGACY["neuronpedia"])


def probe_cfg() -> dict:
    """run.yaml probe block with the profile's probe overrides on top (layer_candidates are model-specific)."""
    run = yaml.safe_load((CFG / "run.yaml").read_text())["probe"]
    return {**run, **(models().get("probe") or {})}


def sae_layer() -> int:
    return int(models()["sae"]["layer"])


def hook_names(layer: int | None = None) -> dict:
    """Canonical hook name -> reader, keyed on the configured SAE layer (was a literal table keyed on 31)."""
    L = sae_layer() if layer is None else int(layer)
    return {f"blocks.{L}.hook_resid_post": "block_output",
            f"layers.{L}.hidden_states_only": "post_ffn_norm",
            f"layers.{L}.mlp_output": "mlp",
            f"layers.{L}.attn_output": "attn",
            f"layers.{L}.input_resid": "block_input"}


def decoder_layers(hf_or_envoy, path: str | None = None):
    """The decoder ModuleList under a HF model (or its nnsight envoy). Gemma-2 keeps it at model.layers; Gemma-3's
    multimodal class nests it (model.language_model.layers). The configured path is tried first, then the known
    ones; the count must equal the text config's num_hidden_layers, or this raises rather than hook a wrong list."""
    tried = [path or replay_cfg()["layers_path"], "model.layers", "model.language_model.layers",
             "language_model.model.layers", "model.model.layers"]
    want = text_config_value(getattr(hf_or_envoy, "config", None), "num_hidden_layers")
    for p in dict.fromkeys(tried):
        obj = hf_or_envoy
        try:
            for part in p.split("."):
                obj = getattr(obj, part)
            n = len(obj)
        except (AttributeError, TypeError):
            continue
        if want is None or n == want:
            return obj
    raise RuntimeError(f"no decoder layer list with {want} layers at any of {tried}; set replay.layers_path")


def text_config_value(cfg, key):
    """A text-model field from a HF config, whether flat (Gemma-2) or nested under text_config (Gemma-3)."""
    if cfg is None:
        return None
    if getattr(cfg, key, None) is not None:
        return getattr(cfg, key)
    tc = getattr(cfg, "text_config", None)
    return getattr(tc, key, None) if tc is not None else None


def check_tokenizer(tok) -> dict:
    """The profile's token facts against the tokenizer on the box. Raises on any disagreement: a wrong stop id
    lets generation run past the end of a turn (audit B-1, 1.7 % of 9B continuations)."""
    s = serializer()
    eot = tok.convert_tokens_to_ids(s.END_OF_TURN)
    have = sorted({int(eot), int(tok.eos_token_id)})
    want = sorted(set(stop_token_ids()))
    if have != want:
        raise RuntimeError(f"stop_token_ids {want} in {models_path().name} != tokenizer's "
                           f"[{s.END_OF_TURN}, eos] = {have}")
    suffix_ids = tok(s.TURN_SUFFIX, add_special_tokens=False)["input_ids"]
    if not suffix_ids or suffix_ids[0] != eot:
        raise RuntimeError(f"TURN_SUFFIX {s.TURN_SUFFIX!r} does not start with {s.END_OF_TURN} id {eot}: {suffix_ids}")
    return {"stop_token_ids": want, "end_of_turn_id": int(eot), "turn_suffix_ids": suffix_ids}


def template_agreement(tok) -> dict:
    """Our serializer vs the tokenizer's own chat template on a fixed conversation (audit B.5-4: the template hash
    pinned a template the serializer never used). Reported in the manifest; a mismatch is a finding to read, since
    the hand-built serializer is the instrument and the template is only the model's training format."""
    conv = [{"role": "system", "content": "You are a careful assistant."}, {"role": "user", "content": "Say hi."},
            {"role": "assistant", "content": "Hi."}, {"role": "user", "content": "Again."}]
    ours = serializer().serialize_messages(conv)
    try:
        theirs = tok.apply_chat_template(conv, tokenize=False, add_generation_prompt=True)
    except Exception as e:  # noqa: BLE001
        return {"agree": None, "error": type(e).__name__ + ": " + str(e)[:120]}
    bos = getattr(tok, "bos_token", "") or ""
    t = theirs[len(bos):] if bos and theirs.startswith(bos) else theirs
    return {"agree": ours == t, "ours_sha": hashlib.sha256(ours.encode()).hexdigest()[:16],
            "template_sha": hashlib.sha256(t.encode()).hexdigest()[:16],
            **({} if ours == t else {"ours_head": ours[:160], "template_head": t[:160]})}

