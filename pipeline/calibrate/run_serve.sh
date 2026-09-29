#!/usr/bin/env bash
# SERVING POD driver, any model profile (27B parameterization, 2026-09-29). One A100 80 GB for Gemma-3-27B-IT in bf16.
#   MODEL_PROFILE=gemma-3-27b-it bash calibrate/run_serve.sh <phase> <out_dir> [seeds] [n]
# phases:
#   ladder       G0's vLLM side (t1_ladder --stage vllm) + a reach test (n=1, seeds 0-4, all scenarios, NOT pinned: the
#                pins are computed by this pod's preflight and committed before the pinned phases run)
#   calibration  the G1 mixed-dtype calibration run (rules 2026-09-29.2): seeds 0-4 x 4 scenarios x 3 variants x n=5 = 300
#                continuations, pinned, never analysed; also the throughput measurement that sizes every later deadline
#   t3           a T3 burst: the given seed block, n per cell (default 10), pinned
# Every phase: pod-side self-stop registered on <out_dir> first (calibrate/pod_selfstop.py), isolation probe, weight
# preflight, vLLM from the profile (calibrate/model_env.sh), DONE written at the end (the copy-back window starts).
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
PHASE=${1:?phase: ladder|calibration|t3}; OUT=${2:?out dir}; SEEDS=${3:-0-4}; N=${4:-}
mkdir -p "$OUT" /workspace/logs
source calibrate/model_env.sh || exit 7
export VLLM_VERSION=$(/workspace/venv_vllm/bin/python -c 'import vllm; print(vllm.__version__)' 2>/dev/null)
LOG=/workspace/logs/serve_${PHASE}_$(date -u +%Y%m%dT%H%MZ)
selfstop_register "$OUT" "${STALL_MIN:-30}" || exit 8
echo "== 0: isolation probe"
command -v bwrap >/dev/null || { apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq bubblewrap >/dev/null 2>&1 || true; }
python -m harness.isolation_probe 2>&1 | tee "${LOG}_isolation.log"
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: no confinement backend passed the canaries. Nothing generated."; exit 2; }
echo "== 0b: weight preflight (prints the weight/SAE identities to pin in the profile)"
python -m calibrate.preflight_weights --hash 2>&1 | tee "${LOG}_preflight.log"
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: weight preflight failed."; exit 5; }
echo "== 0c: tokenizer facts vs the profile, and the serializer vs the model's own chat template"
python - <<'PY' 2>&1 | tee "${LOG}_tokenizer.json"
import json, modelcfg
from transformers import AutoTokenizer
tm = modelcfg.target()
tok = AutoTokenizer.from_pretrained(tm["hf_id"], revision=tm.get("revision"))
print(json.dumps({"check_tokenizer": modelcfg.check_tokenizer(tok), "template_agreement": modelcfg.template_agreement(tok)}, indent=1))
PY
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: the profile's token facts disagree with the tokenizer (see above)."; exit 9; }
gpu_free || true
echo "== 1: vLLM ($TARGET_HF_ID, $TARGET_SERVED_DTYPE)"
vllm_serve "${LOG}_vllm.log" || exit 3
PIN=--require-pinned
case "$PHASE" in
  ladder)
    python -m calibrate.t1_ladder --stage vllm --out "$OUT/t1" || { echo "STOP: t1 vllm stage failed"; exit 3; }
    rm -rf ../scenarios/build_reach
    (cd ../scenarios && python scripts/render.py --arm a --seeds 0-4 --out build_reach) | tail -2
    python -m harness.run_harness --build ../scenarios/build_reach --runs-root "$OUT/reach" --n 1 2>&1 | tee "${LOG}_harness.log"
    ;;
  calibration)
    rm -rf ../scenarios/build_cal
    (cd ../scenarios && python scripts/render.py --arm a --seeds 0-4 --out build_cal) | tail -2
    python -m harness.run_harness --build ../scenarios/build_cal --runs-root "$OUT/runs" --n "${N:-5}" $PIN 2>&1 | tee "${LOG}_harness.log"
    ;;
  t3)
    rm -rf ../scenarios/build_t3
    (cd ../scenarios && python scripts/render.py --arm a --seeds "$SEEDS" --out build_t3) | tail -2
    python -m harness.run_harness --build ../scenarios/build_t3 --runs-root "$OUT/runs" --n "${N:-10}" $PIN 2>&1 | tee "${LOG}_harness.log"
    ;;
  *) echo "unknown phase $PHASE"; exit 1 ;;
esac
kill "$VPID" 2>/dev/null; sleep 5; gpu_free || true
echo "== throughput (sizes the next deadline): continuations per hour from the generation files"
python - "$OUT" <<'PY' | tee "$OUT/throughput.json"
import json, os, sys
from pathlib import Path
rows, ts = 0, []
for f in Path(sys.argv[1]).rglob("generation/**/*.jsonl"):
    rows += sum(1 for l in open(f) if l.strip()); ts.append(os.stat(f).st_mtime)
start = min((os.stat(p).st_mtime for p in Path(sys.argv[1]).rglob("manifest.json")), default=None)
hours = (max(ts) - start) / 3600 if ts and start else None
print(json.dumps({"continuations": rows, "hours": hours and round(hours, 2), "per_hour": hours and round(rows / hours, 1)}))
PY
for RUN in $(ls -d "$OUT"/runs/*/ "$OUT"/reach/*/ 2>/dev/null); do
  python -m gates.run_gates --nogpu --run-dir "$RUN" --gates G7 2>&1 | tee "$RUN/gates_g7.txt"
done
touch "$OUT/DONE"
echo "== done ($PHASE); DONE written: the pod terminates itself after the copy-back window unless re-registered"
