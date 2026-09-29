#!/usr/bin/env bash
# REPLAY POD driver, any model profile (27B parameterization, 2026-09-29). fp32 Gemma-3-27B-IT is ~110 GB: two A100 80 GB
# with the model split across them (replay.device_map: auto), or one H200; the replay card is part of the G1 calibration's
# identity (TF32 kernels differ by architecture), so the card that calibrates is the card that replays everything G1 judges.
#   MODEL_PROFILE=gemma-3-27b-it bash calibrate/run_replay.sh <phase> <dir> [calibration_out]
# phases:
#   ladder       <dir> = the serving pod's ladder out: t1_ladder nnsight (G0 teacher-forced, G1 fixture, G2 decoys) +
#                identity (hf_hidden_states for the 27B) + gates G0,G1,G2,G3
#   calibration  <dir> = the calibration RUN dir: fp32 protocol replay, HF bf16 crosscheck replay, template-defect replay
#                (60 rows), then gates/g1_calibrate build -> [calibration_out] (commit it and pin its sha256 in run.yaml)
#   replay       <dir> = a judged RUN dir: fp32 replay + G1 under the committed calibration
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
PHASE=${1:?phase: ladder|calibration|replay}; D=${2:?dir}
source calibrate/model_env.sh || exit 7
export T1_DTYPE="$REPLAY_DTYPE" T1_TF32=${T1_TF32:-1}
LOG=/workspace/logs/replay_${PHASE}_$(date -u +%Y%m%dT%H%MZ)
nvidia-smi --query-gpu=name,memory.total --format=csv | tee "${LOG}_gpus.txt"
selfstop_register "$D" "${STALL_MIN:-45}" || exit 8
echo "== 0b: weight preflight"
python -m calibrate.preflight_weights --hash 2>&1 | tail -3
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: weight preflight failed."; exit 5; }
case "$PHASE" in
  ladder)
    python -m calibrate.t1_ladder --stage nnsight --out "$D/t1" --gates G0,G1 2>&1 | tee "${LOG}_nnsight.log"
    python -m calibrate.t1_ladder --stage identity --out "$D/t1" 2>&1 | tee "${LOG}_identity.log"
    python -m gates.run_gates --transcripts "$D/t1/transcripts" --replayed "$D/t1/replayed" --features "$D/t1/features" \
      --gates G0,G1,G2,G3 2>&1 | tee "$D/t1/gates_ladder.txt"
    ;;
  calibration)
    RUN=$D; CAL=${3:?calibration output file (e.g. results/<dir>/g1_calibration.json)}
    python -m replay.replay --go --run-dir "$RUN" 2>&1 | tee "${LOG}_fp32.log"
    [ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: fp32 replay failed"; exit 6; }
    echo "== crosscheck: the same rows replayed by HF in the SERVED dtype ($TARGET_SERVED_DTYPE)"
    T1_DTYPE="$TARGET_SERVED_DTYPE" T1_TF32=0 python -m replay.replay --go --transcripts "$RUN/generation" \
      --replayed "$RUN/replay_hf_${TARGET_SERVED_DTYPE}" --features "$RUN/features_hf_${TARGET_SERVED_DTYPE}" 2>&1 | tee "${LOG}_crosscheck.log"
    [ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: crosscheck replay failed"; exit 6; }
    echo "== planted template defect (drop the newline after <start_of_turn>model), 3 rows per file"
    REPLAY_TEMPLATE_DEFECT=drop_model_newline python -m replay.replay --go --transcripts "$RUN/generation" --limit 3 \
      --replayed "$RUN/replay_template_defect" --features "$RUN/features_template_defect" 2>&1 | tee "${LOG}_template.log"
    [ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: template-defect replay failed"; exit 6; }
    python -m gates.g1_calibrate build --transcripts "$RUN/generation" --replay "$RUN/replay" \
      --crosscheck "$RUN/replay_hf_${TARGET_SERVED_DTYPE}" --template-replay "$RUN/replay_template_defect" --out "$CAL" 2>&1 | tee "${LOG}_build.log"
    sha256sum "$CAL" | tee "${CAL}.sha256"
    ;;
  replay)
    RUN=$D
    python -m replay.replay --go --run-dir "$RUN" 2>&1 | tee "${LOG}_fp32.log"
    [ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: replay failed"; exit 6; }
    python -m gates.run_gates --run-dir "$RUN" --gates G1 2>&1 | tee "$RUN/gates_g1.txt"
    ;;
  *) echo "unknown phase $PHASE"; exit 1 ;;
esac
touch "$D/DONE"
echo "== done ($PHASE); DONE written"
