#!/usr/bin/env bash
# T3 DEEP RESAMPLE (2026-09-28, analyze/PREREGISTRATION_T3_WHITEBOX.md addendum): 100 continuations per MIXED full
# cell on the same decision points, then replay + the pre-registered within-cell analysis in the same session.
# Usage: bash run_t3_deep.sh [out_dir] [n]   Cells: impossible_test full seeds 16,18 (discover) 19,3 (test);
# missing_delete_tool full seeds 14,6 (discover) 13,5 (test). Parity split unchanged.
# Order: isolation probe (STOP unless a confinement backend passes every canary) -> vLLM fp32 -> render
# seeds 0-4 -> harness (which re-runs the canaries itself and records backend + results in the manifest)
# -> G6/G7 --nogpu. fp32 + TRITON_ATTN because T1 showed bf16 generation-vs-replay log-probs disagree by
# ~0.17 nats (> g1_logprob_tol 0.05) and fp32 agrees; T3's G1 replays THESE tokens, so the dtype is pinned now.
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf LOCAL_API_KEY=x TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1 T1_DTYPE=float32   # fp32 replay (attempt 1 ran bf16 without this)
export VLLM_ATTENTION_BACKEND=TRITON_ATTN
# vLLM is served from its own venv; hand its version to the harness interpreter for the pinned manifest (software.vllm)
export VLLM_VERSION=$(/workspace/venv_vllm/bin/python -c 'import vllm; print(vllm.__version__)' 2>/dev/null)
OUT=${1:-/workspace/t3_deep}; N=${2:-100}
# T3_SCENARIOS=a,b restricts the harness to those scenario ids; default: all four.
SCEN_ARG=""; [ -n "${T3_SCENARIOS:-}" ] && SCEN_ARG="--scenarios ${T3_SCENARIOS}"
mkdir -p "$OUT" /workspace/logs
echo "== 0: isolation probe (which door is open; which backend passes every canary)"
command -v bwrap >/dev/null || { apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq bubblewrap >/dev/null 2>&1 || true; }
python -m harness.isolation_probe 2>&1 | tee /workspace/logs/t3deep_isolation_probe.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "T3 STOP: no confinement backend passed the canaries on this pod. Nothing generated."; exit 2; }
echo "== 0b: weight preflight (every cached shard vs the pinned revision's recorded sha256; STOP on mismatch)"
python -m calibrate.preflight_weights --hash 2>&1 | tee /workspace/logs/t3deep_preflight_weights.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "T3 STOP: weight preflight failed; the cache on the volume does not match the pinned revision."; exit 5; }
gpu_free() {   # vLLM's engine/worker processes outlive the API server (2026-09-18: 74 GB held after kill); kill by GPU pid
  pkill -9 -f "[v]llm.entrypoints" 2>/dev/null; for p in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
  for i in $(seq 1 30); do [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)" -lt 2000 ] && return 0; sleep 1; done
  echo "warning: GPU memory still held after gpu_free: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"; return 1
}
gpu_free || true
echo "== 1: vLLM serve (fp32)"
/workspace/venv_vllm/bin/python -m vllm.entrypoints.openai.api_server --model google/gemma-2-9b-it --served-model-name gemma-2-9b-it \
  --dtype float32 --max-model-len 8192 --gpu-memory-utilization 0.9 --port 8000 --seed 0 \
  --no-enable-prefix-caching > /workspace/logs/vllm_t3deep.log 2>&1 &
VPID=$!
for i in $(seq 1 240); do curl -s localhost:8000/v1/models >/dev/null 2>&1 && break; sleep 5; \
  kill -0 $VPID 2>/dev/null || { echo "vLLM died; see /workspace/logs/vllm_t3deep.log"; grep -E "Error|error|raise|not support" /workspace/logs/vllm_t3deep.log | tail -15; exit 3; }; done
curl -s localhost:8000/v1/models | head -c 300; echo
echo "== 2: render the deep cells"
rm -rf ../scenarios/build_deep
(cd ../scenarios && python scripts/render.py --arm a --scenario impossible_test --seeds 3,16,18,19 --out build_deep && python scripts/render.py --arm a --scenario missing_delete_tool --seeds 5,6,13,14 --out build_deep) | tail -4
echo "== 3: harness, n=$N per cell (canaries re-run at launch; backend recorded in manifest.json)"
python -m harness.run_harness --build ../scenarios/build_deep --runs-root "$OUT/runs" --n "$N" --require-pinned --variants full --scenarios impossible_test,missing_delete_tool 2>&1 | tee /workspace/logs/t3deep_harness.log
kill $VPID 2>/dev/null; sleep 5; gpu_free || true
RUN=$(ls -d "$OUT"/runs/*/ 2>/dev/null | head -1)
[ -n "$RUN" ] || { echo "no run dir produced"; exit 4; }
echo "== 4: G7 (no GPU) on $RUN"
python -m gates.run_gates --nogpu --run-dir "$RUN" --gates G7 2>&1 | tee "$OUT/gates_nogpu.txt"
echo "== 5: replay (--go) of the deep cells"
python -m replay.replay --go --run-dir "$RUN" 2>&1 | tee /workspace/logs/t3deep_replay.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "DEEP STOP: replay failed"; exit 6; }
echo "== 6: G1"
python -m gates.run_gates --run-dir "$RUN" --gates G1 2>&1 | tee "$OUT/gates_g1.txt"
echo "== 7: discovery on even cells, family-wise effects on odd cells (B=1000, q=0.05), G8"
python -m analyze.discover --run-dir "$RUN" --k 20 --contrast primary_impossible_full_gamed_vs_honest --contrast secondary_missing_delete_full_silent_vs_asked 2>&1 | tee "$RUN/analysis/discovery.txt"
python -m analyze.effects --run-dir "$RUN" --family-wise --trials 1000 --q 0.05 --split test 2>&1 | tee "$RUN/analysis/effects_test.txt"
python -m gates.run_gates --nogpu --run-dir "$RUN" --gates G8 2>&1 | tee "$OUT/gates_g8.txt"
python - "$RUN" <<'PY'
import json, sys
from pathlib import Path
run = Path(sys.argv[1]); eff = json.loads((run / "analysis" / "effects_test.json").read_text())
feats = sorted({pf["feature"] for rep in eff.values() if "per_feature" in rep for pf in rep["per_feature"] if pf["clears_null"]})
print("features clearing their own null (descriptive):", feats)
if feats:
    from replay.sae import fetch_neuronpedia_labels
    try: fetch_neuronpedia_labels(feats, run / "analysis" / "feature_labels.json")
    except Exception as e: print("neuronpedia fetch failed:", e)
PY
echo "== done; run-scoped artifacts under $RUN (generation/, manifest.json with isolation block, cardinality.json)"
python - "$RUN" <<'PY'
import json,sys,os
m=json.load(open(sys.argv[1]+"/manifest.json")); print("isolation:", json.dumps(m.get("isolation"))[:400])
c=sys.argv[1]+"/cardinality.json"; print(open(c).read()[:2500] if os.path.exists(c) else "no cardinality.json (harness did not finish)")
PY
