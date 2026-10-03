#!/usr/bin/env bash
# Pack the committed pipeline/ + scenarios/ (pipeline/calibrate/pack.sh: refuses uncommitted changes, stamps HEAD) into
# <repo>/.ship/ship_<shorthash>.tgz (gitignored; kept, not in a scratch dir), copy it to the pod's /workspace, unpack, and
# verify the pod's pipeline/GIT_COMMIT equals local HEAD.
#   tools/runpod_watch/ship.sh <pod_id>
set -euo pipefail
POD=${1:?pod id}
ROOT=$(git rev-parse --show-toplevel); cd "$ROOT"
HERE="$ROOT/tools/runpod_watch"
HEAD=$(git rev-parse HEAD); SHORT=$(git rev-parse --short HEAD)
mkdir -p .ship; TGZ=".ship/ship_$SHORT.tgz"
[ -s "$TGZ" ] || bash pipeline/calibrate/pack.sh "$TGZ"
echo "upload $(du -h "$TGZ" | cut -f1) -> $POD:/workspace/$(basename "$TGZ")"
"$HERE/pod.sh" "$POD" --scp-to "$TGZ" "/workspace/$(basename "$TGZ")"
GOT=$("$HERE/pod.sh" "$POD" "cd /workspace && tar --no-same-owner --no-same-permissions -xzf $(basename "$TGZ") 2>/dev/null; cat pipeline/GIT_COMMIT")
[ "$GOT" = "$HEAD" ] || { echo "STOP: pod has $GOT, local HEAD is $HEAD"; exit 4; }
echo "shipped $SHORT (pod pipeline/GIT_COMMIT verified)"
