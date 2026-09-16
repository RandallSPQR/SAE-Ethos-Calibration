#!/usr/bin/env bash
# Probe track P1-P4 + G9 on the pod. Usage: bash run_probe.sh <run_id>   (artifacts under /workspace/pipeline/runs/<run_id>)
# P1 needs the vLLM endpoint; P2/P4 need the GPU for nnsight, so vLLM is torn down after P1.
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false
RUN_ID=${1:-t1probe}
mkdir -p runs/$RUN_ID /workspace/logs
cp -n /workspace/t1/features/model_checksum.json runs/$RUN_ID/ 2>/dev/null; mkdir -p runs/$RUN_ID/features; cp -n /workspace/t1/features/model_checksum.json runs/$RUN_ID/features/ 2>/dev/null
if [ ! -s runs/$RUN_ID/probe/lottery/trials.jsonl ]; then
  echo "== P1: vLLM serve + trials"
  /workspace/venv_vllm/bin/python -m vllm.entrypoints.openai.api_server --model google/gemma-2-9b-it --served-model-name gemma-2-9b-it \
    --dtype bfloat16 --max-model-len 2048 --gpu-memory-utilization 0.85 --port 8000 --seed 0 \
    --no-enable-prefix-caching > /workspace/logs/vllm_probe.log 2>&1 &
  VPID=$!
  for i in $(seq 1 180); do curl -s localhost:8000/v1/models >/dev/null 2>&1 && break; sleep 5; \
    kill -0 $VPID 2>/dev/null || { echo "vLLM died"; tail -20 /workspace/logs/vllm_probe.log; exit 2; }; done
  python -m probe.synth_trials --run-dir runs/$RUN_ID || { echo "PROBE_FAIL P1"; kill $VPID; exit 3; }
  kill $VPID; sleep 5; pkill -f "vllm.entrypoints" 2>/dev/null; sleep 5
fi
echo "== P2: extract"
python -m probe.extract --run-dir runs/$RUN_ID || { echo "PROBE_FAIL P2"; exit 4; }
echo "== P3: train"
python -m probe.train --run-dir runs/$RUN_ID || { echo "PROBE_FAIL P3 (heldout below threshold)"; exit 5; }
echo "== P4: calibrate"
python -m probe.calibrate --run-dir runs/$RUN_ID || { echo "PROBE_FAIL P4"; exit 6; }
python -m gates.run_gates --run-dir runs/$RUN_ID --gates G9
echo "PROBE_DONE"
