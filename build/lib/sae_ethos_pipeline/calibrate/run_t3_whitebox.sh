#!/usr/bin/env bash
# T3 white-box session (pre-registration: pipeline/analyze/PREREGISTRATION_T3_WHITEBOX.md). Usage:
#   bash run_t3_whitebox.sh /workspace/t3_joined      (a run dir with generation/, manifest.json, cardinality.json)
# Order: weight preflight -> smoke replay (4 continuations/file, instrument sample 48 spans -> G2/G3/G5 reports on real
# spans) -> gates G2,G3,G5 -> full replay (--go, per-uid sums, replay metadata) -> G1 at 100% cardinality -> discovery
# on even seeds (K=20 per contrast) -> family-wise effects on odd seeds (B=1000, q=0.05) -> G8 -> Neuronpedia labels for
# features that cleared the null. No vLLM: the replay path is nnsight over the HF weights in fp32.
set -uo pipefail
cd /workspace/pipeline
source /workspace/venv/bin/activate
export HF_HOME=/workspace/hf TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1 T1_DTYPE=float32
RUN=${1:-/workspace/t3_joined}
IDENTITY_FROM=${IDENTITY_FROM:-/workspace/pipeline/results/t1_2026-09-16/features/sae_health.json}
mkdir -p /workspace/logs
echo "== 0b: weight preflight"
python -m calibrate.preflight_weights --hash 2>&1 | tee /workspace/logs/t3wb_preflight.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "WB STOP: weight preflight failed"; exit 5; }
echo "== 1: smoke replay + instrument sample (real spans)"
rm -rf "$RUN/features_smoke" "$RUN/replay_smoke"
python -m replay.replay --go --transcripts "$RUN/generation" --replayed "$RUN/replay_smoke" --features "$RUN/features_smoke" \
  --limit 2 --instrument-sample 48 --identity-from "$IDENTITY_FROM" 2>&1 | tee /workspace/logs/t3wb_smoke.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "WB STOP: smoke replay failed"; exit 3; }
echo "== 2: instrument gates on real spans (G2, G3, G5)"
python -m gates.run_gates --transcripts "$RUN/generation" --replayed "$RUN/replay_smoke" --features "$RUN/features_smoke" --gates G2,G3,G5 2>&1 | tee "$RUN/gates_instrument.txt"
echo "== 3: full replay (--go), all scenarios"
rm -rf "$RUN/features" "$RUN/replay"
python -m replay.replay --go --run-dir "$RUN" 2>&1 | tee /workspace/logs/t3wb_replay.log
[ "${PIPESTATUS[0]}" = "0" ] || { echo "WB STOP: full replay failed"; exit 4; }
cp "$RUN"/features_smoke/*.json "$RUN/features/" 2>/dev/null || true          # the real-span G2/G3/G5 reports travel with the store
echo "== 4: G1 at 100% cardinality"
python -m gates.run_gates --run-dir "$RUN" --gates G1 2>&1 | tee "$RUN/gates_g1.txt"
echo "== 5: discovery on even seeds"
python -m analyze.discover --run-dir "$RUN" --k 20 \
  --contrast primary_impossible_full_gamed_vs_honest --contrast secondary_missing_delete_full_silent_vs_asked \
  --contrast tertiary_all_destructive_vs_benign 2>&1 | tee "$RUN/analysis/discovery.txt"
echo "== 6: family-wise effects on odd seeds (B=1000, q=0.05)"
python -m analyze.effects --run-dir "$RUN" --family-wise --trials 1000 --q 0.05 --split test 2>&1 | tee "$RUN/analysis/effects_test.txt"
echo "== 7: G8 (tertiary contrast is the evaluable one; the concept index carries all three)"
python -m gates.run_gates --nogpu --run-dir "$RUN" --gates G8 2>&1 | tee "$RUN/gates_g8.txt"
echo "== 8: Neuronpedia labels for features that cleared the null"
python - "$RUN" <<'PY'
import json, sys
from pathlib import Path
run = Path(sys.argv[1]); eff = json.loads((run / "analysis" / "effects_test.json").read_text())
feats = sorted({pf["feature"] for rep in eff.values() if "per_feature" in rep for pf in rep["per_feature"] if pf["clears_null"]})
print("features clearing the null:", feats)
if feats:
    from replay.sae import fetch_neuronpedia_labels
    try:
        fetch_neuronpedia_labels(feats, run / "analysis" / "feature_labels.json"); print("labels ->", run / "analysis" / "feature_labels.json")
    except Exception as e:
        print("neuronpedia fetch failed:", e)
PY
echo "== done; artifacts: $RUN/{features/*.json,features/**/*_uidsums.parquet,replay/,analysis/,gates_*.txt}; position store stays on the volume"
