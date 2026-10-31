#!/usr/bin/env bash
# ITEM 6 STEERING POD (gate rules 2026-10-03.1, registered and vectors frozen before this pod). One 2 x A100 80 GB pod:
# fp32 (TF32) replay over both GPUs; no vLLM. probe.run_steering does, in order: frozen-vector check, option ids, batch gate,
# HF-hook path check, lambda-0 check per task, timing probe (STOPs if the projection exceeds BUDGET minutes), naturalness,
# then per site (primary first) sweeps, sampled agreement and coherence, then the analysis; DONE starts the copy-back window.
#   MODEL_PROFILE=gemma-3-27b-it bash calibrate/run_steering.sh <out_dir> <frozen vectors dir> <run-2 dir> <budget_min>
# <run-2 dir> = the probe-transfer run 2 on the volume (/workspace/27b/probe_transfer2): native trials + activations.
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
OUT=${1:?out dir}; VEC=${2:?frozen vectors dir}; RUN2=${3:?run-2 dir}; BUDGET=${4:?budget minutes}
mkdir -p "$OUT" /workspace/logs
source calibrate/model_env.sh || exit 7
nvidia-smi --query-gpu=name,memory.total --format=csv | tee "$OUT/gpus.txt"
selfstop_register "$OUT" "${STALL_MIN:-30}" || exit 8
echo "== 0: weight preflight"
python -m calibrate.preflight_weights --hash 2>&1 | tail -3
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: weight preflight failed."; touch "$OUT/DONE"; exit 5; }
[ -s "$RUN2/probe/lottery/trials.jsonl" ] && [ -s "$RUN2/probe/lottery/activations.npz" ] || { echo "STOP: run-2 native data missing at $RUN2"; touch "$OUT/DONE"; exit 6; }
export T1_DTYPE="$REPLAY_DTYPE" T1_TF32=${T1_TF32:-1}
echo "== steering (budget $BUDGET min)"
python -m probe.run_steering --vectors "$VEC" --run-dir "$RUN2" --out "$OUT" --budget-min "$BUDGET"
rc=$?
[ -f "$OUT/DONE" ] || touch "$OUT/DONE"
echo "== exit $rc; DONE written (copy-back window starts)"
exit $rc
