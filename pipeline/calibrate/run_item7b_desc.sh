#!/usr/bin/env bash
# ITEM 7 PHASE B, SECOND PASS (rules 2026-10-07.2; analyze/PREREG_ITEM7B_TEXT_EFFECT.md section 5): the three descriptive
# arms only, on the same pod after the gate run. Waits for the gate run's DONE, then registers this pass with the pod-side
# self-stop (its DONE file replaces the gate's, so the gate's copy-back window does not end the pod mid-pass), serves vLLM
# again (the gate script stops it), and runs harness.run_item7b --only descriptive with the gate's measured cell times
# (--prior-progress) under the same deadline guard. The scenarios rendered for the gate (build_item7b, seeds 0-19) are reused.
#   MODEL_PROFILE=gemma-3-27b-it bash calibrate/run_item7b_desc.sh <gate_out_dir> <out_dir> <deadline UTC ISO>
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
GATE=${1:?gate out dir}; OUT=${2:?out dir}; DEADLINE=${3:?deadline UTC ISO}
mkdir -p "$OUT" /workspace/logs
source calibrate/model_env.sh || exit 7
export VLLM_VERSION=$(/workspace/venv_vllm/bin/python -c 'import vllm; print(vllm.__version__)' 2>/dev/null)
LOG=/workspace/logs/item7b_desc_$(date -u +%Y%m%dT%H%MZ)
echo "== waiting for the gate run's DONE ($GATE/DONE)"
while [ ! -e "$GATE/DONE" ]; do sleep 20; done
echo "== gate DONE seen at $(date -u +%H:%M:%S)"
selfstop_register "$OUT" "${STALL_MIN:-30}" || exit 8
PRIOR=$(ls "$GATE"/runs/*/item7b_progress.jsonl 2>/dev/null | head -1)
[ -s "$PRIOR" ] || { echo "STOP: no gate progress file under $GATE/runs"; touch "$OUT/DONE"; exit 11; }
[ -d ../scenarios/build_item7b ] || { echo "STOP: ../scenarios/build_item7b missing"; touch "$OUT/DONE"; exit 12; }
gpu_free || true
echo "== 1: vLLM ($TARGET_HF_ID, $TARGET_SERVED_DTYPE)"
vllm_serve "${LOG}_vllm.log" || { touch "$OUT/DONE"; exit 3; }
echo "== 2: descriptive pass (rules 2026-10-07.2, deadline $DEADLINE, prior $PRIOR)"
python -m harness.run_item7b --only descriptive --prior-progress "$PRIOR" --build ../scenarios/build_item7b \
  --runs-root "$OUT/runs" --require-pinned --deadline "$DEADLINE" 2>&1 | tee "${LOG}_harness.log"
kill "$VPID" 2>/dev/null; sleep 5; gpu_free || true
echo "== 3: analysis"
RUNDIR=$(ls -d "$OUT"/runs/*/ | head -1)
python -m analyze.item7b_text_effect --run-dir "$RUNDIR" --out "$OUT/analysis" 2>&1 | tail -20
touch "$OUT/DONE"
echo "== done; DONE written (copy-back window starts)"
