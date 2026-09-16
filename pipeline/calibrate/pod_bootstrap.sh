#!/usr/bin/env bash
# One-time pod setup for T1. Idempotent. Run as:  bash /workspace/pipeline/calibrate/pod_bootstrap.sh
# Assumes: HF_TOKEN in env (RunPod secret), repo tarballs already in /workspace.
set -euo pipefail
cd /workspace
export PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HOME=/workspace/hf
mkdir -p "$HF_HOME" logs
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
python --version
if [ ! -x venv/bin/python ]; then python -m venv --system-site-packages venv; fi
source venv/bin/activate
pip install -q -U pip
# vLLM brings its own torch pin; install it first, then the interp stack on top.
# PIN: vllm==0.11.0 -> torch==2.8.0 whose default wheel is cu128. Unpinned vllm (0.29) pulled torch 2.13
# built for CUDA 13, which the RunPod hosts' 12.8 driver (570.x) rejects at torch.cuda init.
pip install -q "vllm==0.11.0" 2>&1 | tail -3
pip install -q "nnsight<0.8" "sae-lens" "peft" "pyarrow" "openai" "pyyaml" "accelerate" "datasets" 2>&1 | tail -3
python - <<'PY'
import torch, transformers, vllm, nnsight, sae_lens, peft
print("torch", torch.__version__, "cuda", torch.version.cuda, "gpu", torch.cuda.get_device_name(0))
print("transformers", transformers.__version__, "| vllm", vllm.__version__, "| nnsight", nnsight.__version__,
      "| sae_lens", sae_lens.__version__, "| peft", peft.__version__)
PY
# weights (gated: needs a HF login). SKIP_WEIGHTS=1 to stop before this step.
if [ "${SKIP_WEIGHTS:-0}" = "1" ]; then echo "BOOTSTRAP_ENV_OK (weights skipped)"; exit 0; fi
python - <<'PY'
import os
from huggingface_hub import snapshot_download, whoami
print("HF user:", whoami()["name"])
p = snapshot_download("google/gemma-2-9b-it", allow_patterns=["*.json", "*.safetensors", "tokenizer*"])
print("gemma-2-9b-it ->", p)
print("sae ->", snapshot_download("google/gemma-scope-9b-it-res", allow_patterns=["layer_31/width_16k/average_l0_76/*"]))
print("oracle ->", snapshot_download("adamkarvonen/checkpoints_latentqa_cls_past_lens_addition_gemma-2-9b-it"))
PY
echo BOOTSTRAP_OK
