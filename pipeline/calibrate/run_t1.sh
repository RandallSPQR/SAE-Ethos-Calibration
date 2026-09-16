#!/usr/bin/env bash
# T1 ladder end-to-end on the pod. Stage 1 serves with vLLM and records greedy generations; stage 2
# tears vLLM down and runs the nnsight/SAE/oracle checks + gates; stage 3 is the TransformerLens tensor
# identity check (own process). Usage: bash run_t1.sh [out_dir] [--fp32]
#   --fp32: both paths in float32 (vLLM --dtype float32, T1_DTYPE=float32) to show the G1 flip is numerical.
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false
OUT=${1:-/workspace/t1}
VDTYPE=bfloat16
if [ "${2:-}" = "--fp32" ]; then VDTYPE=float32; export T1_DTYPE=float32; OUT=${OUT}_fp32; fi
mkdir -p "$OUT" /workspace/logs
if [ ! -s "$OUT/features/t1_vllm.json" ]; then
  echo "== stage 1: vLLM serve"
  /workspace/venv_vllm/bin/python -m vllm.entrypoints.openai.api_server --model google/gemma-2-9b-it --served-model-name gemma-2-9b-it \
    --dtype $VDTYPE --max-model-len 2048 --gpu-memory-utilization 0.9 --port 8000 --seed 0 \
    --no-enable-prefix-caching > /workspace/logs/vllm.log 2>&1 &
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
echo "== stage 3: tensor identity (TransformerLens, no weight processing)"
python -m calibrate.t1_ladder --stage identity --out "$OUT" 2>&1 | tee /workspace/logs/t1_identity.log
python -m gates.run_gates --transcripts "$OUT/transcripts" --replayed "$OUT/replayed" --features "$OUT/features" --gates G0,G1,G2,G3,G4,G5
echo "== done; artifacts in $OUT/features"
ls "$OUT/features"
