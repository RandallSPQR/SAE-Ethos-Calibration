#!/usr/bin/env bash
# Copy results back: tar the given paths (relative to /workspace) on the pod, scp to <repo>/.ship/, extract into <local_dir>.
#   tools/runpod_watch/fetch.sh <pod_id> <local_dir> <path under /workspace>...
set -euo pipefail
POD=${1:?pod id}; DEST=${2:?local dir}; shift 2
ROOT=$(git rev-parse --show-toplevel); HERE="$ROOT/tools/runpod_watch"
NAME="back_${POD}_$(date -u +%Y%m%dT%H%MZ).tgz"
"$HERE/pod.sh" "$POD" "cd /workspace && tar -czf /workspace/$NAME $* && ls -la /workspace/$NAME"
mkdir -p "$ROOT/.ship" "$DEST"
"$HERE/pod.sh" "$POD" --scp-from "/workspace/$NAME" "$ROOT/.ship/$NAME"
tar -xzf "$ROOT/.ship/$NAME" -C "$DEST"
echo "fetched -> $DEST (archive kept at .ship/$NAME)"
