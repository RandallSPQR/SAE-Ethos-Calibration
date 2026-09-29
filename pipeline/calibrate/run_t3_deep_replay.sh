#!/usr/bin/env bash
# T3 DEEP RESAMPLE, replay-only (2026-09-29): the generation of run_b918a1e43585 stands (700 continuations, 7 cells, G7 reach
# recorded); attempt 1's replay ran in bf16 because run_t3_deep.sh did not export T1_DTYPE (G1 worst gap 0.49 nats, median
# 0.12; the fp32 white-box replay sits at 0.004). This re-runs replay + G1 + discovery + effects + G8 + labels in fp32 on the
# existing run dir, moving the bf16 artifacts aside as *_bf16_attempt1. Usage: bash run_t3_deep_replay.sh <run_dir>
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1 T1_DTYPE=float32
RUN=${1:?run dir}
[ -d "$RUN/generation" ] || { echo "no generation under $RUN"; exit 4; }
for d in replay features analysis; do [ -d "$RUN/$d" ] && [ ! -d "$RUN/${d}_bf16_attempt1" ] && mv "$RUN/$d" "$RUN/${d}_bf16_attempt1"; done
for f in gates_g1.txt gates_g8.txt; do [ -f "$(dirname $RUN)/../$f" ] && mv "$(dirname $RUN)/../$f" "$(dirname $RUN)/../${f%.txt}_bf16_attempt1.txt"; done
mkdir -p "$RUN/analysis"
echo "== 0b: weight preflight"
python -m calibrate.preflight_weights --hash 2>&1 | tail -2
[ "${PIPESTATUS[0]}" = "0" ] || { echo "STOP: weight preflight failed"; exit 5; }
echo "== 5: replay (--go, fp32) of the deep cells"
python -m replay.replay --go --run-dir "$RUN" 2>&1 | tee /workspace/logs/t3deep_replay_fp32.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "DEEP STOP: replay failed"; exit 6; }
echo "== 6: G1"
python -m gates.run_gates --run-dir "$RUN" --gates G1 2>&1 | tee "$(dirname $RUN)/../gates_g1.txt"
echo "== 7: discovery on even cells, family-wise effects on odd cells (B=1000, q=0.05), G8"
python -m analyze.discover --run-dir "$RUN" --k 20 --contrast primary_impossible_full_gamed_vs_honest --contrast secondary_missing_delete_full_silent_vs_asked 2>&1 | tee "$RUN/analysis/discovery.txt"
python -m analyze.effects --run-dir "$RUN" --family-wise --trials 1000 --q 0.05 --split test 2>&1 | tee "$RUN/analysis/effects_test.txt"
python -m gates.run_gates --nogpu --run-dir "$RUN" --gates G8 2>&1 | tee "$(dirname $RUN)/../gates_g8.txt"
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
echo "== done; fp32 replay artifacts under $RUN (replay/, features/, analysis/); bf16 attempt kept as *_bf16_attempt1"
