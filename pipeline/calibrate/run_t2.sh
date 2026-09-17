#!/usr/bin/env bash
# T2: short 9B generation with the REAL harness (Arm A), pilot: seeds 0-4, all four scenarios, --n
# continuations each. Usage: bash run_t2.sh [out_dir] [n]
# Order: isolation probe (STOP if the box cannot namespace) -> vLLM fp32 on a unix socket -> render seeds
# 0-4 -> harness inside bwrap -> G6/G7 --nogpu -> copy artifacts. fp32 + TRITON_ATTN because T1 showed bf16
# generation vs replay log-probs disagree by ~0.17 nats (> g1_logprob_tol 0.05) and fp32 agrees; T3's G1
# replays THESE tokens, so the generation dtype is pinned now.
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export VLLM_ATTENTION_BACKEND=TRITON_ATTN
OUT=${1:-/workspace/t2}; N=${2:-10}; UDS=/workspace/vllm.sock
mkdir -p "$OUT" /workspace/logs
echo "== 0: isolation probe"
python -m harness.launch_isolated --probe --uds "$UDS" 2>&1 | tee /workspace/logs/t2_isolation_probe.log
RC=${PIPESTATUS[0]}
if [ "$RC" != "0" ]; then
  # bwrap needs the binary AND a kernel/container that allows user namespaces; try installing once, then re-probe
  command -v bwrap >/dev/null || { apt-get update -qq && apt-get install -y -qq bubblewrap; }
  python -m harness.launch_isolated --probe --uds "$UDS" 2>&1 | tee -a /workspace/logs/t2_isolation_probe.log
  [ "${PIPESTATUS[0]}" = "0" ] || { echo "T2 STOP: this pod cannot isolate real episodes (see log). Nothing generated."; exit 2; }
fi
echo "== 1: vLLM serve (fp32, unix socket $UDS)"
rm -f "$UDS"
/workspace/venv_vllm/bin/python -m vllm.entrypoints.openai.api_server --model google/gemma-2-9b-it --served-model-name gemma-2-9b-it \
  --dtype float32 --max-model-len 4096 --gpu-memory-utilization 0.9 --seed 0 --no-enable-prefix-caching \
  --uds "$UDS" > /workspace/logs/vllm_t2.log 2>&1 &
VPID=$!
for i in $(seq 1 240); do [ -S "$UDS" ] && curl -s --unix-socket "$UDS" http://vllm/v1/models >/dev/null 2>&1 && break; sleep 5; \
  kill -0 $VPID 2>/dev/null || { echo "vLLM died; see /workspace/logs/vllm_t2.log"; grep -E "Error|error|raise|not support" /workspace/logs/vllm_t2.log | tail -15; exit 3; }; done
curl -s --unix-socket "$UDS" http://vllm/v1/models | head -c 300; echo
echo "== 2: render seeds 0-4"
(cd ../scenarios && python scripts/render.py --arm a --seeds 0-4 --out build_t2) | tail -3
echo "== 3: harness (isolated), n=$N per cell"
python -m harness.launch_isolated --uds "$UDS" -- --build ../scenarios/build_t2 --runs-root "$OUT/runs" --n "$N" \
  2>&1 | tee /workspace/logs/t2_harness.log
kill $VPID; sleep 5; pkill -f "vllm.entrypoints" 2>/dev/null
RUN=$(ls -d "$OUT"/runs/*/ 2>/dev/null | head -1)
[ -n "$RUN" ] || { echo "no run dir produced"; exit 4; }
echo "== 4: G6/G7 (no GPU) on $RUN"
python -m gates.run_gates --nogpu --run-dir "$RUN" 2>&1 | tee "$OUT/gates_nogpu.txt"
echo "== done; run-scoped artifacts under $RUN (generation/, manifest.json, cardinality.json)"
cat "$RUN/cardinality.json" | head -40
