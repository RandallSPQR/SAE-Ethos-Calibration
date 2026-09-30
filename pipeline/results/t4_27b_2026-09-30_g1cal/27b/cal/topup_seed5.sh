#!/usr/bin/env bash
# G1 calibration top-up (Randall, 2026-09-30): seed 5, all 12 cells, n=5, into the SAME run (run_id = H(config)),
# because the seed-1 mention prefix was excluded (295 < the 300-row floor of rules 2026-09-29.2).
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1 MODEL_PROFILE=gemma-3-27b-it
OUT=/workspace/27b/cal
rm -f "$OUT/DONE"
source calibrate/model_env.sh || exit 7
export VLLM_VERSION=$(/workspace/venv_vllm/bin/python -c 'import vllm; print(vllm.__version__)' 2>/dev/null)
selfstop_register "$OUT" 30 || exit 8
BEFORE=$(ls -d "$OUT"/runs/*/ | wc -l); RUN0=$(ls -d "$OUT"/runs/*/ | head -1)
echo "== top-up: runs before=$BEFORE ($RUN0)"
gpu_free || true
vllm_serve /workspace/logs/serve_cal_topup_vllm.log || exit 3
rm -rf ../scenarios/build_cal5
(cd ../scenarios && python scripts/render.py --arm a --seeds 5 --out build_cal5) | tail -2
python -m harness.run_harness --build ../scenarios/build_cal5 --runs-root "$OUT/runs" --n 5 --require-pinned 2>&1 | tee /workspace/logs/serve_cal_topup_harness.log
kill "$VPID" 2>/dev/null; sleep 5; gpu_free || true
AFTER=$(ls -d "$OUT"/runs/*/ | wc -l)
echo "== top-up: runs after=$AFTER"
[ "$AFTER" = "$BEFORE" ] || { echo "TOPUP STOP: a second run_id was created; not merging two runs"; touch "$OUT/DONE"; exit 11; }
python - "$RUN0" <<'PY'
import json, glob, sys
rows = sum(1 for f in glob.glob(sys.argv[1] + "/generation/**/*.jsonl", recursive=True) for l in open(f) if l.strip())
print(json.dumps({"calibration_rows": rows}))
PY
python -m gates.run_gates --nogpu --run-dir "$RUN0" --gates G7 2>&1 | tail -3
touch "$OUT/DONE"
echo "== top-up done"
