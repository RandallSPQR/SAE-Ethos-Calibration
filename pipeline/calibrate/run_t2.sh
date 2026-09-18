#!/usr/bin/env bash
# T2: short 9B generation with the REAL harness (Arm A), pilot: seeds 0-4, all four scenarios, --n
# continuations each. Usage: bash run_t2.sh [out_dir] [n]
# Order: isolation probe (STOP unless a confinement backend passes every canary) -> vLLM fp32 -> render
# seeds 0-4 -> harness (which re-runs the canaries itself and records backend + results in the manifest)
# -> G6/G7 --nogpu. fp32 + TRITON_ATTN because T1 showed bf16 generation-vs-replay log-probs disagree by
# ~0.17 nats (> g1_logprob_tol 0.05) and fp32 agrees; T3's G1 replays THESE tokens, so the dtype is pinned now.
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export VLLM_ATTENTION_BACKEND=TRITON_ATTN
OUT=${1:-/workspace/t2}; N=${2:-10}
mkdir -p "$OUT" /workspace/logs
echo "== 0: isolation probe (which door is open; which backend passes every canary)"
command -v bwrap >/dev/null || { apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq bubblewrap >/dev/null 2>&1 || true; }
python -m harness.isolation_probe 2>&1 | tee /workspace/logs/t2_isolation_probe.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "T2 STOP: no confinement backend passed the canaries on this pod. Nothing generated."; exit 2; }
echo "== 0b: weight preflight (every cached shard vs the pinned revision's recorded sha256; STOP on mismatch)"
python -m calibrate.preflight_weights --hash 2>&1 | tee /workspace/logs/t2_preflight_weights.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "T2 STOP: weight preflight failed; the cache on the volume does not match the pinned revision."; exit 5; }
echo "== 1: vLLM serve (fp32)"
/workspace/venv_vllm/bin/python -m vllm.entrypoints.openai.api_server --model google/gemma-2-9b-it --served-model-name gemma-2-9b-it \
  --dtype float32 --max-model-len 8192 --gpu-memory-utilization 0.9 --port 8000 --seed 0 \
  --no-enable-prefix-caching > /workspace/logs/vllm_t2.log 2>&1 &
VPID=$!
for i in $(seq 1 240); do curl -s localhost:8000/v1/models >/dev/null 2>&1 && break; sleep 5; \
  kill -0 $VPID 2>/dev/null || { echo "vLLM died; see /workspace/logs/vllm_t2.log"; grep -E "Error|error|raise|not support" /workspace/logs/vllm_t2.log | tail -15; exit 3; }; done
curl -s localhost:8000/v1/models | head -c 300; echo
echo "== 2: render seeds 0-4"
(cd ../scenarios && python scripts/render.py --arm a --seeds 0-4 --out build_t2) | tail -3
echo "== 3: harness, n=$N per cell (canaries re-run at launch; backend recorded in manifest.json)"
python -m harness.run_harness --build ../scenarios/build_t2 --runs-root "$OUT/runs" --n "$N" 2>&1 | tee /workspace/logs/t2_harness.log
kill $VPID; sleep 5; pkill -f "vllm.entrypoints" 2>/dev/null
RUN=$(ls -d "$OUT"/runs/*/ 2>/dev/null | head -1)
[ -n "$RUN" ] || { echo "no run dir produced"; exit 4; }
echo "== 4: G6/G7 (no GPU) on $RUN"
python -m gates.run_gates --nogpu --run-dir "$RUN" 2>&1 | tee "$OUT/gates_nogpu.txt"
echo "== done; run-scoped artifacts under $RUN (generation/, manifest.json with isolation block, cardinality.json)"
python - "$RUN" <<'PY'
import json,sys,os
m=json.load(open(sys.argv[1]+"/manifest.json")); print("isolation:", json.dumps(m.get("isolation"))[:400])
c=sys.argv[1]+"/cardinality.json"; print(open(c).read()[:2500] if os.path.exists(c) else "no cardinality.json (harness did not finish)")
PY
