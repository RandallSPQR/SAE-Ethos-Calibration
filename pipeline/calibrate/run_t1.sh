#!/usr/bin/env bash
# T1 ladder end-to-end on the pod. Stage 1 serves with vLLM and records greedy generations; stage 2
# tears vLLM down and runs the nnsight/SAE/oracle checks + gates. Usage: bash run_t1.sh [out_dir]
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false
OUT=${1:-/workspace/t1}
mkdir -p "$OUT" /workspace/logs
if [ ! -s "$OUT/features/t1_vllm.json" ]; then
  echo "== stage 1: vLLM serve"
  python -m vllm.entrypoints.openai.api_server --model google/gemma-2-9b-it --served-model-name gemma-2-9b-it \
    --dtype bfloat16 --max-model-len 4096 --gpu-memory-utilization 0.85 --port 8000 --seed 0 \
    --enable-prefix-caching=False > /workspace/logs/vllm.log 2>&1 &
  VPID=$!
  for i in $(seq 1 180); do curl -s localhost:8000/v1/models >/dev/null 2>&1 && break; sleep 5; \
    kill -0 $VPID 2>/dev/null || { echo "vLLM died; see /workspace/logs/vllm.log"; tail -30 /workspace/logs/vllm.log; exit 2; }; done
  curl -s localhost:8000/v1/models | head -c 300; echo
  python -m calibrate.t1_ladder --stage vllm --out "$OUT" || { echo "stage 1 failed"; kill $VPID; exit 3; }
  kill $VPID; sleep 5; pkill -f "vllm.entrypoints" 2>/dev/null; sleep 5
  nvidia-smi --query-gpu=memory.used --format=csv,noheader
fi
echo "== stage 2: nnsight"
python -m calibrate.t1_ladder --stage nnsight --out "$OUT" 2>&1 | tee /workspace/logs/t1_nnsight.log
echo "== done; artifacts in $OUT/features"
ls "$OUT/features"
