#!/usr/bin/env python3
"""create_armed.py: wait for stock, then create a pod and arm BOTH stops in the same process (the watchdog rule).

  create_armed.py --name N --gpu-count 2 --dc EUR-IS-1 --volume u0isne6ams --hours 3 --note "..." \
                  [--every-min 5] [--give-up-hours 8]

Each attempt is a create call (a create refused for capacity bills nothing). On success, in this same process:
  1. the pod was created with the pod-side self-stop in its start command, carrying the deadline computed here;
  2. the Mac watchdog is armed with the same number of hours (tools/runpod_watch/pod_watchdog.py arm);
  3. the pod id and deadline are printed and the process exits 0.
If the pod is never driven afterwards, its self-stop terminates it 45 min after boot (no run registered) and the watchdog
at the deadline. Gives up after --give-up-hours with exit 3 (nothing created).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.runpod.io/v2/pods"
UA = "runpod-watch/1.0 (create_armed)"
HERE = Path(__file__).resolve().parent
WATCHDOG = Path.home() / "runpod_watch" / "app" / "pod_watchdog.py"
IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
SELFSTOP = "/workspace/pipeline/calibrate/pod_selfstop.py"


def _key() -> str:
    return (Path.home() / "runpod_watch" / "api_key").read_text().strip()


def _post(body: dict) -> tuple[int, dict | str]:
    req = urllib.request.Request(API, method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {_key()}", "User-Agent": UA,
                                          "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)[:400]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--gpu", default="NVIDIA A100-SXM4-80GB")
    ap.add_argument("--gpu-count", type=int, default=1)
    ap.add_argument("--dc", required=True)
    ap.add_argument("--volume", required=True)
    ap.add_argument("--hours", type=float, required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--disk", type=int, default=80)
    ap.add_argument("--every-min", type=float, default=5)
    ap.add_argument("--give-up-hours", type=float, default=8)
    a = ap.parse_args()
    t_end = time.time() + a.give_up_hours * 3600
    n = 0
    while time.time() < t_end:
        n += 1
        deadline = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=a.hours)).replace(microsecond=0)
        iso = deadline.isoformat().replace("+00:00", "Z")
        cmd = (f"python3 {SELFSTOP} start --deadline-utc {iso} --note '{a.note}' "
               f"|| echo selfstop-missing > /tmp/selfstop_missing; exec /start.sh")
        body = {"name": a.name, "image": IMAGE, "cloud": "SECURE", "gpu": {"id": a.gpu, "count": a.gpu_count},
                "dataCenterIds": [a.dc], "mounts": {"network": [{"volumeId": a.volume, "path": "/workspace"}]},
                "disk": a.disk, "ports": ["22/tcp"], "startSsh": True, "entrypoint": ["bash", "-c"], "cmd": [cmd]}
        code, resp = _post(body)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
        if code in (200, 201) and isinstance(resp, dict) and resp.get("id"):
            pid = resp["id"]
            r = subprocess.run([sys.executable, str(WATCHDOG), "arm", pid, "--hours", str(a.hours),
                                "--note", f"{a.note} (selfstop {iso}, created by create_armed)"], capture_output=True, text=True)
            print(f"{stamp} CREATED {pid} ${resp.get('cost')}/h deadline {iso}; watchdog: {r.stdout.strip() or r.stderr.strip()}", flush=True)
            sys.exit(0 if r.returncode == 0 else 4)
        print(f"{stamp} attempt {n}: HTTP {code} {str(resp)[:160]}", flush=True)
        time.sleep(a.every_min * 60)
    print("gave up: nothing created", flush=True)
    sys.exit(3)


if __name__ == "__main__":
    main()
