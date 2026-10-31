#!/usr/bin/env python3
"""Load the target model under nnsight for teacher-forced replay.

Backend decision (T1): nnsight's HF backend (`nnsight.LanguageModel`) over the same HF weights vLLM
serves. nnsight's in-vLLM backend pins a specific vllm build and could not be assumed to attach on the
rented image; G0 therefore compares vLLM greedy generation against THIS path, which is the observation
path every downstream activation comes from. Attention is forced to `eager` because Gemma-2's logit
soft-capping is only exact under eager attention in transformers.
"""
from dataclasses import dataclass
from pathlib import Path
import yaml

CFG = Path(__file__).resolve().parent.parent / "config"


@dataclass
class LoadedModel:
    model: object          # nnsight.LanguageModel
    tokenizer: object
    n_layers: int
    d_model: int
    layer: int             # resolved SAE/hook layer


def resolve_layer(n_layers, fraction):
    return max(0, min(n_layers - 1, round(n_layers * fraction)))


def models_cfg():
    import modelcfg
    return modelcfg.models()


def load_target(which="target", device="cuda"):
    """which: 'target' (IT model) or 'base' (pretrained, for §4.5.3.4)."""
    import torch
    import nnsight
    models = models_cfg()
    tm = models["target_model"]
    hf_id = tm["hf_id" if which == "target" else "base_hf_id"]
    import os
    import modelcfg
    rc = modelcfg.replay_cfg()
    # replay dtype: the profile's replay.dtype (float32), overridable by T1_DTYPE for a deliberate other-dtype replay
    # (the G1 calibration's served-dtype crosscheck). models.yaml target_model.dtype is the SERVING dtype, not this.
    dtype = getattr(torch, os.environ.get("T1_DTYPE") or rc["dtype"])
    if os.environ.get("T1_TF32") == "1":
        # fp32 storage with TF32 tensor-core matmuls (~8x faster on A100). Allowed only where a gate proves
        # the numbers still meet tolerance (probe.batch_gate re-run under T1_TF32=1 before P4 uses it).
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        print("TF32 matmuls ENABLED (T1_TF32=1)")
    # device_map: 'cuda' (one card) or 'auto' (the 27B fp32 replay split over two cards; layers run in sequence)
    dev = os.environ.get("REPLAY_DEVICE_MAP") or (rc["device_map"] if device == "cuda" else device)
    kw = dict(device_map=dev, torch_dtype=dtype, attn_implementation=rc["attn_implementation"], dispatch=True)
    if tm.get("revision"):
        kw["revision"] = tm["revision"]
    lm = getattr(nnsight, rc["nnsight_class"])(hf_id, **kw)      # LanguageModel (Gemma-2) / VisionLanguageModel (Gemma-3)
    cfg = lm.config
    tok = getattr(lm, "tokenizer", None)
    if tok is None:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(hf_id, revision=tm.get("revision"))
        lm.tokenizer = tok
    layer = int(models["sae"]["layer"])          # LOCKED in models.yaml; never computed from a fraction
    modelcfg.check_tokenizer(tok)                # stop ids / turn suffix in the profile vs this tokenizer; raises
    return LoadedModel(model=lm, tokenizer=tok, n_layers=modelcfg.text_config_value(cfg, "num_hidden_layers"),
                       d_model=modelcfg.text_config_value(cfg, "hidden_size"), layer=layer)


# Names in models.yaml (sae.hook_point / sae.hook_candidates) -> how to read that tensor off the HF
# Gemma-2 block under nnsight. The decoys are DISTINCT tensors of the same shape:
#   input_resid          : residual stream entering the block (hook_resid_pre)
#   mlp_output           : raw MLP output, before post_feedforward_layernorm
#   attn_output          : raw attention output, before post_attention_layernorm
#   hidden_states_only   : post_feedforward_layernorm(mlp) -- the block's own increment WITHOUT the
#                          residual, i.e. the vLLM-style "hidden_states" half of (hidden_states, residual)
# The chosen hook is the block's full output = residual stream leaving the block (see hooks.resid_post).
# Keyed on the profile's SAE layer (modelcfg.hook_names); was a literal table keyed on 31.
def hook_readers():
    import modelcfg
    return modelcfg.hook_names()


def decoder_layers(lm: LoadedModel):
    """The decoder ModuleList (Gemma-2: model.layers; Gemma-3: model.language_model.layers), count-checked."""
    import modelcfg
    return modelcfg.decoder_layers(lm.model)


def residual_module(lm: LoadedModel, hook_point=None):
    """nnsight handle for the decoder block named by sae.hook_point (layer index locked in the profile)."""
    return decoder_layers(lm)[lm.layer]


def hook_reader(hook_name):
    try:
        return hook_readers()[hook_name]
    except KeyError:
        raise KeyError(f"no reader for hook {hook_name!r} at the profile's SAE layer; see modelcfg.hook_names")
