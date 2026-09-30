# Source from a driver (27B parameterization, 2026-09-29):  source calibrate/model_env.sh
# Exports the active model profile (MODEL_PROFILE -> config/models_<profile>.yaml; unset = the 9B models.yaml) as the
# variables every driver uses, and defines vllm_serve, so no driver names a model, dtype or context length itself
# (audit C: the .sh drivers each hard-coded --model google/gemma-2-9b-it --dtype float32 --max-model-len 8192).
# TARGET_SERVED_DTYPE is the ONE variable passed to `vllm serve --dtype` AND read by the harness into
# sampling.served_dtype and by provenance into model.served_dtype (rules 2026-09-29.2: G1 selects its criterion from it).
eval "$(python - <<'PY'
import shlex
import modelcfg
s, r = modelcfg.serve_cfg(), modelcfg.replay_cfg()
env = {"MODEL_PROFILE": modelcfg.profile() or "", "MODEL_FAMILY": modelcfg.family(),
       "TARGET_HF_ID": s["model"], "TARGET_SERVED_NAME": s["served_model_name"], "TARGET_REVISION": s["revision"] or "",
       "TARGET_SERVED_DTYPE": s["dtype"], "TARGET_MAX_MODEL_LEN": s["max_model_len"], "TARGET_TP": s["tensor_parallel_size"],
       "TARGET_VLLM_EXTRA": s["extra_args"], "TARGET_ATTN_BACKEND": s.get("attention_backend") or "",
       "REPLAY_DTYPE": r["dtype"], "REPLAY_DEVICE_MAP": r["device_map"]}
for k, v in env.items():
    print(f"export {k}={shlex.quote(str(v))}")
PY
)" || { echo "model_env: could not read the model profile (MODEL_PROFILE=${MODEL_PROFILE:-})"; return 7 2>/dev/null || exit 7; }
[ -n "$TARGET_ATTN_BACKEND" ] && export VLLM_ATTENTION_BACKEND="$TARGET_ATTN_BACKEND"
echo "model profile: ${MODEL_PROFILE:-default} family=$MODEL_FAMILY model=$TARGET_HF_ID@${TARGET_REVISION:0:12} served=$TARGET_SERVED_DTYPE replay=$REPLAY_DTYPE ($REPLAY_DEVICE_MAP) ctx=$TARGET_MAX_MODEL_LEN"

# vllm_serve LOGFILE: start the OpenAI server for the profile in the background, wait until it answers, set VPID.
vllm_serve() {
  local log=${1:?log file}
  local rev=(); [ -n "$TARGET_REVISION" ] && rev=(--revision "$TARGET_REVISION" --tokenizer-revision "$TARGET_REVISION")
  # shellcheck disable=SC2086  # TARGET_VLLM_EXTRA is a flag list from the profile
  /workspace/venv_vllm/bin/python -m vllm.entrypoints.openai.api_server --model "$TARGET_HF_ID" "${rev[@]}" \
    --served-model-name "$TARGET_SERVED_NAME" --dtype "$TARGET_SERVED_DTYPE" --max-model-len "$TARGET_MAX_MODEL_LEN" \
    --tensor-parallel-size "$TARGET_TP" --gpu-memory-utilization 0.9 --port 8000 --seed 0 $TARGET_VLLM_EXTRA > "$log" 2>&1 &
  VPID=$!
  for _ in $(seq 1 360); do
    curl -s localhost:8000/v1/models >/dev/null 2>&1 && { curl -s localhost:8000/v1/models | head -c 300; echo; return 0; }
    kill -0 "$VPID" 2>/dev/null || { echo "vLLM died; see $log"; grep -E "Error|error|raise|not support" "$log" | tail -15; return 3; }
    sleep 5
  done
  echo "vLLM did not answer in 30 min; see $log"; return 3
}

# gpu_free: vLLM's engine/worker processes outlive the API server (2026-09-18: 74 GB held after kill); kill by GPU pid.
gpu_free() {
  pkill -9 -f "[v]llm.entrypoints" 2>/dev/null
  for p in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
  for _ in $(seq 1 30); do [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | sort -n | tail -1)" -lt 2000 ] && return 0; sleep 1; done
  echo "warning: GPU memory still held after gpu_free: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"; return 1
}

# selfstop_register OUT [STALL_MIN]: the pod-side stop watches OUT from here on (calibrate/pod_selfstop.py). Refuses to
# continue when the daemon is not running: a long burst without the pod-side stop is what the RUNBOOK forbids.
selfstop_register() {
  local out=${1:?out dir}; local stall=${2:-30}
  # a DONE left by an earlier pod in this directory would read as "finished hours ago" and end this pod at once
  [ -e "$out/DONE" ] && { echo "selfstop: removing stale $out/DONE ($(stat -c %y "$out/DONE" 2>/dev/null))"; rm -f "$out/DONE"; }
  python calibrate/pod_selfstop.py check || { [ "${ALLOW_NO_SELFSTOP:-0}" = "1" ] || { echo "STOP: pod-side self-stop not verified (see check above). ALLOW_NO_SELFSTOP=1 overrides for a short hands-on step only."; return 8; }; }
  python calibrate/pod_selfstop.py watch --dir "$out" --done-file "$out/DONE" --stall-min "$stall" || [ "${ALLOW_NO_SELFSTOP:-0}" = "1" ]
}
