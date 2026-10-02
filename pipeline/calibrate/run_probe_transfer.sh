#!/usr/bin/env bash
# PROBE-REGIME TRANSFER POD (queue item 5; gate rules 2026-10-02.1 pre-registered before this pod). One 2 x A100 80 GB pod:
#   P1  vLLM serves the profile's target (bf16, GPU 0); the probe trials are sampled in BOTH regimes: native (the Fan et al.
#       single user turn, probe/<task>) and agent (the same items under the harness's agent template, probe_agent/<task>)
#   P2  vLLM torn down; fp32 replay over both GPUs: prompt-final residuals at probe.layer_candidates, both regimes
#   P3  (offline, $0, after copy-back) python -m probe.transfer --run-dir <run>
#   MODEL_PROFILE=gemma-3-27b-it bash calibrate/run_probe_transfer.sh <run_dir> [concurrency]
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
RUN=${1:?run dir}; CONC=${2:-16}
mkdir -p "$RUN" /workspace/logs
source calibrate/model_env.sh || exit 7
LOG=/workspace/logs/probe_transfer_$(date -u +%Y%m%dT%H%MZ)
nvidia-smi --query-gpu=name,memory.total --format=csv | tee "${LOG}_gpus.txt"
selfstop_register "$RUN" "${STALL_MIN:-30}" || exit 8
echo "== 0: weight preflight"
python -m calibrate.preflight_weights --hash 2>&1 | tail -3
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: weight preflight failed."; exit 5; }
if [ ! -s "$RUN/probe_agent/ultimatum/trials.jsonl" ]; then
  echo "== P1: vLLM ($TARGET_SERVED_DTYPE, GPU 0) + trials, native then agent (concurrency $CONC)"
  CUDA_VISIBLE_DEVICES=0 vllm_serve "${LOG}_vllm.log" || exit 3
  python -m probe.synth_trials --run-dir "$RUN" --regime native --concurrency "$CONC" 2>&1 | tee "${LOG}_p1_native.log"
  [ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: no task has a dial in the native regime (see baseline.json); nothing to transfer"; gpu_free; touch "$RUN/DONE"; exit 4; }
  python -m probe.synth_trials --run-dir "$RUN" --regime agent --concurrency "$CONC" 2>&1 | tee "${LOG}_p1_agent.log"
  [ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: agent-regime trials truncated at max_tokens (see probe_agent/*/baseline.json)"; kill "$VPID" 2>/dev/null; gpu_free; touch "$RUN/DONE"; exit 9; }
  kill "$VPID" 2>/dev/null; sleep 5; gpu_free
fi
echo "== P2: fp32 prompt-final residuals, both regimes (device_map $REPLAY_DEVICE_MAP)"
export T1_DTYPE="$REPLAY_DTYPE" T1_TF32=${T1_TF32:-1}
for REG in native agent; do
  python -m probe.extract --run-dir "$RUN" --regime "$REG" 2>&1 | tee "${LOG}_p2_${REG}.log"
  [ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: extraction failed ($REG)"; touch "$RUN/DONE"; exit 6; }
done
echo "== P3 (on the pod too, for the record; the committed run is the offline one)"
python -m probe.transfer --run-dir "$RUN" 2>&1 | tail -20
touch "$RUN/DONE"
echo "== done; DONE written (copy-back window starts)"
