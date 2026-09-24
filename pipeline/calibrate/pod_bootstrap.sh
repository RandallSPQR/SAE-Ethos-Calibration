#!/usr/bin/env bash
# Pod setup. Idempotent. Run as:  bash /workspace/pipeline/calibrate/pod_bootstrap.sh
# Since 2026-09-18 /workspace is the NETWORK VOLUME (EUR-IS-1, id u0isne6ams, 150 GB): the venvs, HF_HOME
# (weights + token) and the pipeline tarball persist across pods, so after the first pod this script is a
# version check plus the weight preflight. The HF token is a file on the volume: $HF_HOME/token, root-owned,
# mode 600, under a 0700 HF_HOME; the harness's disk-secret canary verifies an episode uid cannot read it.
# First pod only: log in interactively (browser OAuth; pasting a token failed 3x on 2026-09-16):
#     HF_HOME=/workspace/hf hf auth login      -> "Log in with your browser"
# then re-run this script. SKIP_WEIGHTS=1 stops before the download/preflight.
set -euo pipefail
cd /workspace
export PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HOME=/workspace/hf
mkdir -p "$HF_HOME" logs
chmod 700 "$HF_HOME"; [ -f "$HF_HOME/token" ] && chmod 600 "$HF_HOME/token" || true
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
python --version
if [ ! -x venv/bin/python ]; then python -m venv --system-site-packages venv; fi
source venv/bin/activate
pip install -q -U pip
# vLLM brings its own torch pin; install it first, then the interp stack on top.
# PIN: vllm==0.11.0 -> torch==2.8.0 whose default wheel is cu128. Unpinned vllm (0.29) pulled torch 2.13
# built for CUDA 13, which the RunPod hosts' 12.8 driver (570.x) rejects at torch.cuda init.
# vLLM lives in ITS OWN venv: vllm 0.11.0 needs transformers<5 (GemmaTokenizer.all_special_tokens_extended
# was removed in transformers 5), while nnsight 0.7 pulls transformers 5.x. The two stages are separate
# processes, so the server runs from venv_vllm and everything else from venv.
if [ ! -x venv_vllm/bin/python ]; then python -m venv venv_vllm; fi
venv_vllm/bin/pip install -q -U pip
venv_vllm/bin/pip install -q "vllm==0.11.0" "transformers<5" 2>&1 | tail -3
# pytest: the harness runs the rendered repos' tests under the episode uid from THIS venv; the T2 probe's
# positive canary (imports_available) failed on 2026-09-17 because it was missing here.
pip install -q "torch==2.8.0" "torchvision==0.23.0" "torchaudio==2.8.0" "nnsight<0.8" "sae-lens" "peft" "pyarrow" "openai" "pyyaml" "accelerate" "datasets" "transformer-lens" "pytest>=7.0" "flake8" "pylint" 2>&1 | tail -3
python - <<'PY'
import torch, transformers, nnsight, sae_lens, peft
print("torch", torch.__version__, "cuda", torch.version.cuda, "gpu", torch.cuda.get_device_name(0))
print("transformers", transformers.__version__, "| nnsight", nnsight.__version__,
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
chmod 700 "$HF_HOME"; chmod 600 "$HF_HOME/token"
# weight identity against the pinned revision's recorded hashes (cached weights are never trusted unchecked)
(cd /workspace/pipeline && python -m calibrate.preflight_weights --hash) || { echo "BOOTSTRAP STOP: weight preflight failed"; exit 5; }
echo BOOTSTRAP_OK
