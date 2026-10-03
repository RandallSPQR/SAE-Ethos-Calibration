#!/usr/bin/env bash
# Start a long job on the pod fully detached from the SSH session (setsid -f, all three fds redirected), so the local
# ssh returns at once and a killed local shell cannot take the job down. Prints the first log lines.
#   tools/runpod_watch/run_detached.sh <pod_id> <remote_log> '<command, run from /workspace/pipeline>'
set -euo pipefail
POD=${1:?pod id}; LOG=${2:?remote log path}; CMD=${3:?command}
HERE=$(cd "$(dirname "$0")" && pwd)
"$HERE/pod.sh" "$POD" "mkdir -p \$(dirname $LOG) && cd /workspace/pipeline && setsid -f bash -c $(printf %q "$CMD") > $LOG 2>&1 < /dev/null; sleep 5; head -5 $LOG"
