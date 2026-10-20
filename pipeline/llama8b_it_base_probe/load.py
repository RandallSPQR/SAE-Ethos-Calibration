"""Load the pinned model named by the active profile (MODEL_PROFILE_FILE) on the Transformers path: AutoModelForCausalLM
at the profile's revision, the profile's attention implementation (eager), TF32 off, eval mode; the tokenizer is
checked against the profile's stop ids and turn suffix (modelcfg.check_tokenizer) before anything runs. # box-side"""
import modelcfg


def load_model(dtype="bfloat16", device_map="cuda"):
    """-> (tokenizer, model). dtype 'bfloat16' (capture) or 'float32' (a protocol replay)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tm, rc = modelcfg.target(), modelcfg.replay_cfg()
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    tok = AutoTokenizer.from_pretrained(tm["hf_id"], revision=tm["revision"])
    model = AutoModelForCausalLM.from_pretrained(tm["hf_id"], revision=tm["revision"], dtype=getattr(torch, dtype),
                                                 attn_implementation=rc["attn_implementation"], device_map=device_map)
    model.eval()
    modelcfg.check_tokenizer(tok)
    return tok, model
