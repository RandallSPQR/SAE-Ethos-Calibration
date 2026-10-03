#!/usr/bin/env bash
# Terminate a pod this session created, confirm it is gone from the account's pod list, and disarm its watchdog entry.
# Prints the pod's hourly price and start time (read before deletion) so the cost can be reported.
#   tools/runpod_watch/terminate.sh <pod_id>
set -euo pipefail
POD=${1:?pod id}
ROOT=$(git rev-parse --show-toplevel); K=$(cat "$HOME/runpod_watch/api_key")
curl -s -H "Authorization: Bearer $K" "https://rest.runpod.io/v1/pods/$POD" |
  /usr/bin/python3 -c "import json,sys; d=json.load(sys.stdin); print('pod', d.get('id'), d.get('name'), 'costPerHr', d.get('costPerHr'), 'lastStartedAt', d.get('lastStartedAt'))" || true
curl -s -o /dev/null -w "DELETE %{http_code}\n" -X DELETE -H "Authorization: Bearer $K" "https://rest.runpod.io/v1/pods/$POD"
for i in 1 2 3 4 5 6; do
  left=$(curl -s -H "Authorization: Bearer $K" https://rest.runpod.io/v1/pods | /usr/bin/python3 -c "import json,sys; print(sum(p.get('id') == '$POD' for p in json.load(sys.stdin)))")
  [ "$left" = "0" ] && break; sleep 5
done
[ "$left" = "0" ] || { echo "STOP: $POD still listed after DELETE"; exit 5; }
/usr/bin/python3 "$ROOT/tools/runpod_watch/pod_watchdog.py" disarm "$POD"
echo "terminated $POD at $(date -u +%FT%TZ); pods left: $(curl -s -H "Authorization: Bearer $K" https://rest.runpod.io/v1/pods | /usr/bin/python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
