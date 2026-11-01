#!/bin/bash
# Box-side identity check and server for ONE lineup model on the lineup serving stack (sae-ethos-pipeline 1.2.0).
# NOT RUN when 1.2.0 was cut (no GPU spend); the S0 gate of a study runs it once per model and commits its JSON.
#   LINEUP_MODEL=qwen3.5-9b bash pod/serve_lineup.sh /root/out
# Steps, each STOPs on failure:
#   1 venv_vllm_lineup with the lineup pins (lineup.yaml serving.pins), versions recorded
#   2 weights fetched at the pinned revision; calibrate.preflight_weights --hash must return the lineup weight_hash
#   3 modelcfg.check_tokenizer + template_agreement against the real tokenizer (agree must be true)
#   4 vLLM from lineup-serve-cmd (batch size 1, raw logprobs, seeded, prefix cache off)
#   5 readout repeat probe (8 identical requests, bit-identical) and the exact-fallback consistency check
# Output: $OUT/box_identity_<model>.json. No API key of any kind on the pod except HF_TOKEN (a RunPod secret).
set -euo pipefail
OUT=${1:?out dir}; M=${LINEUP_MODEL:?LINEUP_MODEL}
mkdir -p "$OUT/logs"; export HF_HOME=${HF_HOME:-/root/hf} TOKENIZERS_PARALLELISM=false
PY=${LINEUP_PY:-/root/venv/bin/python}        # the venv holding sae-ethos-pipeline 1.2.0 (+ transformers 5.17.0)
VPY=/root/venv_vllm_lineup/bin/python
eval "$($PY - <<'PYX'
import shlex, sae_ethos_pipeline, lineup
p = lineup.lineup()["serving"]["pins"]
print("PINS=" + shlex.quote(" ".join(f"{k}=={v}" for k, v in p.items())))
PYX
)"
[ -x $VPY ] || { python3 -m venv /root/venv_vllm_lineup && /root/venv_vllm_lineup/bin/pip install -q $PINS > "$OUT/logs/pip_vllm_lineup.log" 2>&1; }
$VPY -c "import vllm, torch, transformers; print(vllm.__version__, torch.__version__, transformers.__version__, torch.cuda.get_device_name(0))" | tee "$OUT/logs/stack_$M.txt"
export MODEL_PROFILE_FILE=$($PY -c "import sae_ethos_pipeline, lineup; print(lineup.profile_path('$M'))")
HFID=$($PY -c "import sae_ethos_pipeline, lineup; print(lineup.models()['$M']['hf_id'])")
REV=$($PY -c "import sae_ethos_pipeline, lineup; print(lineup.models()['$M']['revision'])")
WANT=$($PY -c "import sae_ethos_pipeline, lineup; print(lineup.models()['$M']['weight_hash'])")
$PY -c "from huggingface_hub import snapshot_download as s; s('$HFID', revision='$REV', allow_patterns=['*.json','*.safetensors','tokenizer*','*.jinja','merges.txt','vocab.json'])"
(cd "$($PY -c 'import sae_ethos_pipeline as s; print(s.ROOT)')" && $PY -m calibrate.preflight_weights --repo "$HFID" --revision "$REV" \
   --allow '*.json' '*.safetensors' 'tokenizer*' --hash --out "$OUT/preflight_$M.json") | tee "$OUT/logs/preflight_$M.log"
GOT=$($PY -c "import json; print(json.load(open('$OUT/preflight_$M.json'))['repos'][0]['weight_hash'])")
[ "$GOT" = "$WANT" ] || { echo "STOP: weight_hash $GOT != lineup $WANT"; exit 5; }
$PY - "$OUT/tokenizer_$M.json" <<'PYX'
import json, sys, sae_ethos_pipeline, modelcfg
from transformers import AutoTokenizer
tm = modelcfg.target()
tok = AutoTokenizer.from_pretrained(tm["hf_id"], revision=tm["revision"])
r = {"check_tokenizer": modelcfg.check_tokenizer(tok), "template_agreement": modelcfg.template_agreement(tok)}
json.dump(r, open(sys.argv[1], "w"), indent=1); print(json.dumps(r))
sys.exit(0 if r["template_agreement"].get("agree") else 9)
PYX
CMD=$(LINEUP_VLLM_PYTHON=$VPY "$(dirname "$PY")/lineup-serve-cmd" "$M")   # 1.2.1: `python -m lineup.serve` cannot import lineup from an installed copy
bash -c "$CMD" > "$OUT/logs/vllm_$M.log" 2>&1 & VPID=$!
for _ in $(seq 1 360); do curl -s localhost:8000/v1/models >/dev/null 2>&1 && break; kill -0 $VPID || { tail -30 "$OUT/logs/vllm_$M.log"; exit 3; }; sleep 5; done
$PY - "$M" "$OUT/box_identity_$M.json" "$OUT" <<'PYX'
import json, sys, sae_ethos_pipeline, lineup, modelcfg
from lineup import readout, manifest
from transformers import AutoTokenizer
m, out, d = sys.argv[1:4]
tm = modelcfg.target(); tok = AutoTokenizer.from_pretrained(tm["hf_id"], revision=tm["revision"])
ser = modelcfg.serializer()
conv = [{"role": "user", "content": "Two options, A and B. Evidence: the left door is warm. Answer with one letter: A or B."}]
ids = ser.apply_to_tokenizer(tok, conv)
opt = readout.option_token_ids(tok)
t = readout.HTTPTransport()
served = modelcfg.models()["endpoint"]["served_model_name"]
rep = {"identity_block": manifest.identity_block(m),
       "preflight": json.load(open(f"{d}/preflight_{m}.json"))["repos"][0],
       "tokenizer": json.load(open(f"{d}/tokenizer_{m}.json")),
       "repeat_probe": readout.repeat_probe(t, served, ids, opt, n=8),
       "fallback_consistency": readout.fallback_consistency(t, served, ids, opt)}
rep["ok"] = rep["preflight"]["ok"] and rep["repeat_probe"]["bit_identical"] and rep["fallback_consistency"]["ok"]
json.dump(rep, open(out, "w"), indent=1); print(json.dumps({k: rep[k] for k in ("repeat_probe", "fallback_consistency", "ok")}))
sys.exit(0 if rep["ok"] else 4)
PYX
kill $VPID 2>/dev/null || true
