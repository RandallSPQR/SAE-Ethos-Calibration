import os
os.environ["HF_HOME"] = "/workspace/hf"
from huggingface_hub import snapshot_download, whoami
print("HF user:", whoami()["name"], flush=True)
print("gemma-2-9b-it ->", snapshot_download("google/gemma-2-9b-it", allow_patterns=["*.json", "*.safetensors", "tokenizer*"]), flush=True)
print("sae ->", snapshot_download("google/gemma-scope-9b-it-res", allow_patterns=["layer_31/width_16k/average_l0_76/*"]), flush=True)
print("oracle ->", snapshot_download("adamkarvonen/checkpoints_latentqa_cls_past_lens_addition_gemma-2-9b-it"), flush=True)
print("PULL_OK", flush=True)
