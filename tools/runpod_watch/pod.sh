#!/usr/bin/env bash
# SSH to a RunPod pod by id. The address is resolved from the REST API on every call (it changes per pod), so nothing
# lives in a scratch directory that a cleanup can take away.
#   tools/runpod_watch/pod.sh <pod_id> '<remote command>'     run a command
#   tools/runpod_watch/pod.sh <pod_id> --addr                  print "ip port"
#   tools/runpod_watch/pod.sh <pod_id> --scp-to <local> <remote>   /  --scp-from <remote> <local>
# Key: ~/runpod_watch/api_key (never printed). SSH key: ~/.ssh/runpod_ed25519 (override with RUNPOD_SSH_KEY).
set -euo pipefail
POD=${1:?pod id}; shift
KEY=${RUNPOD_SSH_KEY:-$HOME/.ssh/runpod_ed25519}
read -r IP PORT < <(curl -s -H "Authorization: Bearer $(cat "$HOME/runpod_watch/api_key")" "https://rest.runpod.io/v1/pods/$POD" |
  /usr/bin/python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('publicIp') or '-', (d.get('portMappings') or {}).get('22', '-'))")
[ "$IP" != "-" ] && [ "$PORT" != "-" ] || { echo "pod $POD has no public IP / SSH port yet (or does not exist)" >&2; exit 3; }
OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=20 -o ServerAliveInterval=30 -i "$KEY")
case "${1:-}" in
  --addr) echo "$IP $PORT" ;;
  --scp-to) scp -q "${OPTS[@]}" -P "$PORT" "$2" "root@$IP:$3" ;;
  --scp-from) scp -q "${OPTS[@]}" -P "$PORT" "root@$IP:$2" "$3" ;;
  *) ssh "${OPTS[@]}" -p "$PORT" "root@$IP" "$@" ;;
esac
