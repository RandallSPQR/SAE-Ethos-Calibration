#!/usr/bin/env bash
# Watch a detached pod job until it ends. Exits 0 on DONE, 2 on a STOP line or a traceback, 3 when the log has not
# changed for STALL checks, 4 when the pod is unreachable 3 times running (check the API: it may have self-stopped).
# One status line per check on stdout. A DONE file older than this watcher's start is ignored: a re-run into the same
# directory on the network volume would otherwise read as finished at once (item 9 attempt 2, 2026-10-08: attempt 1's
# DONE was still there, the watcher fired before the pod script removed it, and the pod was terminated unused).
#   tools/runpod_watch/watch_run.sh <pod_id> <remote_done_file> <remote_log> [interval_s=120] [stall_checks=10]
set -uo pipefail
POD=${1:?pod id}; DONE=${2:?done file}; LOG=${3:?log}; EVERY=${4:-120}; STALL=${5:-10}
HERE=$(cd "$(dirname "$0")" && pwd)
last=""; same=0; miss=0; START=$(date -u +%s)
while true; do
  if out=$("$HERE/pod.sh" "$POD" "test -f $DONE && [ \$(stat -c %Y $DONE) -ge $START ] && echo __DONE__; grep -c . $LOG; grep -E 'STOP:|^Traceback' $LOG | tail -3; tail -c 300 $LOG" 2>&1); then
    miss=0
  else
    miss=$((miss + 1)); echo "$(date -u +%H:%M) unreachable ($miss): $(echo "$out" | tail -1)"
    [ $miss -ge 3 ] && { echo "== pod unreachable 3 times"; exit 4; }
    sleep "$EVERY"; continue
  fi
  echo "$(date -u +%H:%M) $(echo "$out" | tail -2 | tr '\n' ' ' | cut -c1-200)"
  echo "$out" | grep -qE 'STOP:|^Traceback' && { echo "== STOP/Traceback"; echo "$out"; exit 2; }
  echo "$out" | grep -q __DONE__ && { echo "== DONE"; exit 0; }
  if [ "$out" = "$last" ]; then same=$((same + 1)); else same=0; last="$out"; fi
  [ $same -ge "$STALL" ] && { echo "== log unchanged for $STALL checks"; echo "$out"; exit 3; }
  sleep "$EVERY"
done
