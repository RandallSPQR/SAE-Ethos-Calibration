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
    return yaml.safe_load((CFG / "models.yaml").read_text())


def load_target(which="target", device="cuda"):
    """which: 'target' (IT model) or 'base' (pretrained, for §4.5.3.4)."""
    import torch
    from nnsight import LanguageModel
    models = models_cfg()
    tm = models["target_model"]
    hf_id = tm["hf_id" if which == "target" else "base_hf_id"]
    import os
    dtype = getattr(torch, os.environ.get("T1_DTYPE") or tm.get("dtype", "bfloat16"))   # T1_DTYPE=float32 for the fp32 G1 check
    if os.environ.get("T1_TF32") == "1":
        # fp32 storage with TF32 tensor-core matmuls (~8x faster on A100). Allowed only where a gate proves
        # the numbers still meet tolerance (probe.batch_gate re-run under T1_TF32=1 before P4 uses it).
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        print("TF32 matmuls ENABLED (T1_TF32=1)")
    kw = dict(device_map=device, torch_dtype=dtype, attn_implementation="eager", dispatch=True)
    if tm.get("revision"):
        kw["revision"] = tm["revision"]
    lm = LanguageModel(hf_id, **kw)
    cfg = lm.config
    layer = int(models["sae"]["layer"])          # LOCKED in models.yaml; never computed from a fraction
    return LoadedModel(model=lm, tokenizer=lm.tokenizer, n_layers=cfg.num_hidden_layers,
                       d_model=cfg.hidden_size, layer=layer)


# Names in models.yaml (sae.hook_point / sae.hook_candidates) -> how to read that tensor off the HF
# Gemma-2 block under nnsight. The decoys are DISTINCT tensors of the same shape:
#   input_resid          : residual stream entering the block (hook_resid_pre)
#   mlp_output           : raw MLP output, before post_feedforward_layernorm
#   attn_output          : raw attention output, before post_attention_layernorm
#   hidden_states_only   : post_feedforward_layernorm(mlp) -- the block's own increment WITHOUT the
#                          residual, i.e. the vLLM-style "hidden_states" half of (hidden_states, residual)
# The chosen hook is the block's full output = residual stream leaving the block (see hooks.resid_post).
HOOK_READERS = {
    "blocks.31.hook_resid_post": "block_output",
    "layers.31.hidden_states_only": "post_ffn_norm",
    "layers.31.mlp_output": "mlp",
    "layers.31.attn_output": "attn",
    "layers.31.input_resid": "block_input",
}


def residual_module(lm: LoadedModel, hook_point=None):
    """nnsight handle for the decoder block named by sae.hook_point (layer index locked in models.yaml)."""
    return lm.model.model.layers[lm.layer]


def hook_reader(hook_name):
    try:
        return HOOK_READERS[hook_name]
    except KeyError:
        raise KeyError(f"no reader for hook {hook_name!r}; add it to modelload.HOOK_READERS")
