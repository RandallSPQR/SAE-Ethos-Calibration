#!/usr/bin/env bash
# ITEM 6b STEERING POD (gate rules 2026-10-06.1, registered and offline vectors frozen before this pod). One 2 x A100 80 GB
# pod: fp32 (TF32) replay over both GPUs; no vLLM. probe.run_steering6b runs the registered order (PREREG_ITEM6B section 10):
# pins, ids, overlap (0), instrument checks, lambda-0 (safe 70), lambda-0 sampled agreement (native, then A/B), manipulation
# floor, CAA build + hash + on-manifold STOP, timing probe (fallbacks: drop Fan, n, relabel), sweeps, steered checks,
# relabeled cross-check, analysis; DONE starts the copy-back window.
#   MODEL_PROFILE=gemma-3-27b-it bash calibrate/run_steering6b.sh <out_dir> <frozen 6b vectors dir> <run-2 dir> <budget_min>
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
OUT=${1:?out dir}; VEC=${2:?frozen 6b vectors dir}; RUN2=${3:?run-2 dir}; BUDGET=${4:?budget minutes}
mkdir -p "$OUT" /workspace/logs
source calibrate/model_env.sh || exit 7
nvidia-smi --query-gpu=name,memory.total --format=csv | tee "$OUT/gpus.txt"
selfstop_register "$OUT" "${STALL_MIN:-30}" || exit 8
echo "== 0: weight preflight"
python -m calibrate.preflight_weights --hash 2>&1 | tail -3
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: weight preflight failed."; touch "$OUT/DONE"; exit 5; }
[ -s "$RUN2/probe/lottery/trials.jsonl" ] && [ -s "$RUN2/probe/lottery/activations.npz" ] || { echo "STOP: run-2 native data missing at $RUN2"; touch "$OUT/DONE"; exit 6; }
export T1_DTYPE="$REPLAY_DTYPE" T1_TF32=${T1_TF32:-1}
echo "== item 6b steering (budget $BUDGET min)"
python -m probe.run_steering6b --vectors "$VEC" --run-dir "$RUN2" --out "$OUT" --budget-min "$BUDGET"
rc=$?
[ -f "$OUT/DONE" ] || touch "$OUT/DONE"
echo "== exit $rc; DONE written (copy-back window starts)"
exit $rc
