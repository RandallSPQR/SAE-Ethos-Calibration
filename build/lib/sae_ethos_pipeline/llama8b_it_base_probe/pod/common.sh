#!/bin/bash
# Shared pod setup, sourced by a project's pod driver. The project's code arrives as a `git archive` tarball of one
# commit at $PROBE_CODE; PROBE_COMMIT names that commit (llama8b_it_base_probe.manifest records it). The project's
# pyproject.toml depends on sae-ethos-pipeline (>= 1.1.0), so installing the project installs this subpackage and its
# console scripts (llama-probe-*).
#   PROBE_COMMIT=<sha> setsid nohup bash $PROBE_CODE/pod/my_driver.sh > /root/driver.nohup 2>&1 < /dev/null &
# with my_driver.sh starting:  source "$(python3 -c 'import sae_ethos_pipeline, llama8b_it_base_probe as p; print(p.ROOT)')/pod/common.sh"
# or, before the venv exists, from a vendored copy. No API key of any kind belongs on a pod: HF_TOKEN is a RunPod secret
# (llama-probe-pods create --secret-env HF_TOKEN=<secret name>).
set -euo pipefail
touch /tmp/pod_driven                                  # the pod-side self-stop: a driver is present (pods.DRIVEN_FILE)
export HF_HOME=${HF_HOME:-/root/hf} HF_HUB_DISABLE_TELEMETRY=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TOKENIZERS_PARALLELISM=false
# RunPod puts the resolved secret (HF_TOKEN) in PID 1's environment; an SSH-started process does not inherit it
HF_TOKEN=$(tr '\0' '\n' < /proc/1/environ | sed -n 's/^HF_TOKEN=//p'); export HF_TOKEN
[ -n "$HF_TOKEN" ] || { echo "HF_TOKEN missing from PID 1 env"; exit 1; }
PROBE_CODE=${PROBE_CODE:-/root/code}
OUT=${PROBE_OUT:-/root/out}; mkdir -p "$OUT/logs" "$OUT/preflight"
PY=/root/venv/bin/python
step() { echo "=== $(date -u +%FT%TZ) $*"; }

setup_replay_venv() {      # the Transformers capture path: torch from the image (2.8.0), pinned transformers/accelerate
  python3 -m venv --system-site-packages /root/venv
  /root/venv/bin/pip install -q "transformers==5.17.0" "accelerate==1.15.0" safetensors scipy "huggingface_hub<2.0" "$PROBE_CODE"
  $PY -c "import torch, transformers; print('torch', torch.__version__, 'transformers', transformers.__version__, torch.cuda.get_device_name(0))"
}

setup_vllm_venv() {        # vLLM 0.11.0 in its own venv (needs transformers<5)
  python3 -m venv /root/venv_vllm
  /root/venv_vllm/bin/pip install -q "vllm==0.11.0" "transformers<5" > "$OUT/logs/pip_vllm.log" 2>&1
  /root/venv_vllm/bin/python -c "import vllm; print('vllm', vllm.__version__)" | tee "$OUT/logs/vllm_version.txt"
}

access_check() {           # repo ids; a "dataset:" prefix checks a dataset
  $PY - "$@" <<'EOF'
import sys
from huggingface_hub import auth_check
bad = []
for r in sys.argv[1:]:
    kind = "dataset" if r.startswith("dataset:") else "model"
    rid = r.split(":", 1)[1] if kind == "dataset" else r
    try:
        auth_check(rid, repo_type=kind); print(rid, "ACCESS OK")
    except Exception as e:
        print(rid, "NO ACCESS", type(e).__name__); bad.append(rid)
sys.exit(1 if bad else 0)
EOF
}

fetch_and_preflight() {    # repo rev "patterns" name: download only those files at the pinned revision, then hash them
  local repo=$1 rev=$2 allow=$3 name=$4
  $PY -c "import sys; from huggingface_hub import snapshot_download; print(snapshot_download(sys.argv[1], revision=sys.argv[2], allow_patterns=sys.argv[3].split()))" "$repo" "$rev" "$allow"
  # shellcheck disable=SC2086
  /root/venv/bin/llama-probe-run calibrate.preflight_weights --repo "$repo" --revision "$rev" --allow $allow --hash \
      --hf-home "$HF_HOME" --out "$OUT/preflight/$name.json"
}

fetch_llama() {            # both pinned Llama-3.1-8B models, as the shipped profiles name them
  fetch_and_preflight meta-llama/Llama-3.1-8B d04e592bb4f6aa9cfee91e2e20afa771667e1d4b "*.safetensors *.json" llama-base
  fetch_and_preflight meta-llama/Llama-3.1-8B-Instruct 0e9e39f249a16976918f6564b8830bc894c89659 "*.safetensors *.json" llama-instruct
}

profile() {                # absolute path of a shipped profile: profile llama-3.1-8b | llama-3.1-8b-it
  $PY -c "import sys, sae_ethos_pipeline, llama8b_it_base_probe as p; print(p.profile_path(sys.argv[1]))" "$1"
}

serve() {                  # hf_id rev served_name logfile -> starts vLLM bf16 in the background, sets VP
  /root/venv_vllm/bin/vllm serve "$1" --revision "$2" --dtype bfloat16 --max-model-len 4096 --served-model-name "$3" \
      --port 8000 --seed 0 --gpu-memory-utilization 0.85 --no-enable-prefix-caching > "$4" 2>&1 &
  VP=$!
}
