#!/usr/bin/env bash
# ITEM 10 POD (grader vs neutral at turn_close; analyze/PREREG_ITEM10_GRADER.md, rules 2026-10-09.1). One A100 80 GB,
# Gemma-3-27B-IT served bf16 by vLLM from the profile, the T3 harness. Same order as calibrate/run_item9.sh: self-stop
# registered first, isolation probe, weight preflight, tokenizer facts + template agreement, the paraphrase length check with
# the served tokenizer, vLLM, render impossible_test seeds 0-55, harness.run_item10 (stray-file preflight, deadline guard),
# the analysis; DONE starts the copy-back window.
#   MODEL_PROFILE=gemma-3-27b-it bash calibrate/run_item10.sh <out_dir> <deadline UTC ISO, e.g. 2026-10-08T03:00:00Z>
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
OUT=${1:?out dir}; DEADLINE=${2:?deadline UTC ISO}
mkdir -p "$OUT" /workspace/logs
source calibrate/model_env.sh || exit 7
export VLLM_VERSION=$(/workspace/venv_vllm/bin/python -c 'import vllm; print(vllm.__version__)' 2>/dev/null)
LOG=/workspace/logs/item10_$(date -u +%Y%m%dT%H%MZ)
selfstop_register "$OUT" "${STALL_MIN:-30}" || exit 8
echo "== 0: isolation probe"
command -v bwrap >/dev/null || { apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq bubblewrap >/dev/null 2>&1 || true; }
python -m harness.isolation_probe 2>&1 | tee "${LOG}_isolation.log"
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: no confinement backend passed the canaries. Nothing generated."; touch "$OUT/DONE"; exit 2; }
echo "== 0b: weight preflight"
python -m calibrate.preflight_weights --hash 2>&1 | tee "${LOG}_preflight.log"
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: weight preflight failed."; touch "$OUT/DONE"; exit 5; }
echo "== 0c: tokenizer facts and template agreement"
python - <<'PY' 2>&1 | tee "${LOG}_tokenizer.json"
import json, modelcfg
from transformers import AutoTokenizer
tm = modelcfg.target()
tok = AutoTokenizer.from_pretrained(tm["hf_id"], revision=tm.get("revision"))
print(json.dumps({"check_tokenizer": modelcfg.check_tokenizer(tok), "template_agreement": modelcfg.template_agreement(tok)}, indent=1))
PY
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: the profile's token facts disagree with the tokenizer."; touch "$OUT/DONE"; exit 9; }
echo "== 0d: paraphrase pair lengths with the served tokenizer (+-2 tokens)"
python -m harness.run_item10 --check-lengths 2>&1 | tee "${LOG}_lengths.json"
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: a paraphrase pair differs by more than 2 tokens."; touch "$OUT/DONE"; exit 10; }
gpu_free || true
echo "== 1: vLLM ($TARGET_HF_ID, $TARGET_SERVED_DTYPE)"
vllm_serve "${LOG}_vllm.log" || { touch "$OUT/DONE"; exit 3; }
rm -rf ../scenarios/build_item10
(cd ../scenarios && python scripts/render.py --arm a --scenario impossible_test --seeds 0-55 --out build_item10) | tail -2
echo "== 2: item 10 episodes (deadline $DEADLINE)"
python -m harness.run_item10 --build ../scenarios/build_item10 --runs-root "$OUT/runs" --require-pinned --deadline "$DEADLINE" 2>&1 | tee "${LOG}_harness.log"
kill "$VPID" 2>/dev/null; sleep 5; gpu_free || true
echo "== 3: analysis"
RUNDIR=$(ls -d "$OUT"/runs/*/ | head -1)
python -m analyze.item10_grader --run-dir "$RUNDIR" --out "$OUT/analysis" 2>&1 | tail -20
touch "$OUT/DONE"
echo "== done; DONE written (copy-back window starts)"
