#!/usr/bin/env python3
"""pod_selfstop.py: the pod terminates itself. A second, pod-side stop behind the Mac watchdog
(tools/runpod_watch/pod_watchdog.py), which does not fire while the laptop sleeps and cannot see a stalled run.
Stdlib only; runs as root on the pod, outside the harness (confine strips RUNPOD_* from every episode env).

Started by the pod's start command AT CREATION, so it runs before SSH exists (the 2026-09-25 pod idled ~20 h
waiting for a session that never came):

  plan     (Mac) --hours H [--note ...]    print the deadline, the create-pod start command and the watchdog arm
                                           line, all carrying the same deadline
  start    (pod) --deadline-utc ISO        write the config and fork the daemon
  watch    (pod) --dir D [--dir D2] [--done-file F] [--stall-min M]
                                           the driver registers its output; any new file under D counts as progress
  hold     (pod)                           pause the stall check for 60 min (renewable; expires on its own, so a
                                           forgotten hold cannot keep an idle pod alive; the deadline is never held)
  check    (pod)                           which terminate paths exist (runpodctl, RUNPOD_API_KEY + own-pod read)
  status   (pod)
  stop-now (pod) --why TEXT

Terminate when ANY of:
  1. now >= deadline (the same deadline armed on the Mac);
  2. no activity for stall_min (default 30) once a driver has registered, or for startup_min (default 45) before
     one has. Activity = the newest file mtime under the watched dirs, a `watch`/`hold`/`start` call, or an
     unexpired hold;
  3. the done file exists and after_done_min (default 45, the copy-back window) have passed since it appeared.
Each termination writes /workspace/logs/selfstop_<pod>_final.json (the volume outlives the pod) and tries, in
order: `runpodctl remove pod $RUNPOD_POD_ID`, DELETE https://api.runpod.io/v2/pods/<id> (the watchdog's call), DELETE
https://rest.runpod.io/v1/pods/<id>. On failure it logs and retries every tick; the Mac watchdog is still armed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE = Path(os.environ.get("SELFSTOP_DIR", "/tmp/selfstop"))          # pod-local: the volume is shared across pods
CONFIG, HOLD, PIDFILE, LOGF = STATE / "config.json", STATE / "hold", STATE / "daemon.pid", STATE / "selfstop.log"
FINAL_DIR = Path(os.environ.get("SELFSTOP_FINAL_DIR", "/workspace/logs"))
HOLD_MIN = 60.0
UA = "sae-ethos-selfstop/1.0"                                          # Cloudflare 403s the default Python UA
SCRIPT_ON_VOLUME = "/workspace/pipeline/calibrate/pod_selfstop.py"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _pod() -> str:
    return os.environ.get("RUNPOD_POD_ID", "unknown-pod")


def _log(msg: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    line = f"{_now().isoformat(timespec='seconds')} {msg}"
    with open(LOGF, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _load() -> dict:
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def _save(c: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=1))
    tmp.replace(CONFIG)


def _touch_activity(c: dict, why: str) -> dict:
    c["last_call"] = time.time()
    c["last_call_why"] = why
    return c


def newest_mtime(dirs) -> float:
    best = 0.0
    for d in dirs:
        p = Path(d)
        if not p.exists():
            continue
        for root, _, files in os.walk(p):
            for fn in files:
                try:
                    best = max(best, os.stat(os.path.join(root, fn)).st_mtime)
                except OSError:
                    pass
    return best


def hold_active(now_ts: float, scale: float = 1.0) -> bool:
    try:
        return now_ts - HOLD.stat().st_mtime < HOLD_MIN * 60 * scale
    except OSError:
        return False


def decide(c: dict, now_ts: float) -> str | None:
    """The reason to terminate now, or None. Pure given the config, the clock and the filesystem."""
    scale = float(c.get("time_scale", 1.0))                            # tests run minutes as seconds
    if now_ts >= dt.datetime.fromisoformat(c["deadline_utc"]).timestamp():
        return f"deadline {c['deadline_utc']} passed"
    done = c.get("done_file")
    if done and Path(done).exists():
        waited = now_ts - Path(done).stat().st_mtime
        if waited >= c.get("after_done_min", 45) * 60 * scale:
            return f"run done ({done}) and the {c.get('after_done_min', 45)}-min copy-back window passed"
    if hold_active(now_ts, scale):
        return None
    last = max(c.get("started", 0), c.get("last_call", 0), newest_mtime(c.get("watch", [])))
    limit = (c.get("stall_min", 30) if c.get("watch") else c.get("startup_min", 45)) * 60 * scale
    if now_ts - last >= limit:
        what = f"no new file under {c['watch']}" if c.get("watch") else "no driver registered (`watch`) or hold"
        return f"stalled: {what} for {(now_ts - last) / 60 / scale:.0f} min (limit {limit / 60 / scale:.0f})"
    return None


def _http_delete(url: str, key: str) -> int:
    req = urllib.request.Request(url, method="DELETE", headers={"Authorization": f"Bearer {key}", "User-Agent": UA,
                                                                "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return -1


def _http_get(url: str, key: str) -> int:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return -1


def terminate(why: str, dry_run: bool) -> bool:
    pod = _pod()
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"pod": pod, "at": _now().isoformat(timespec="seconds"), "why": why, "config": _load(), "attempts": []}
    if dry_run:
        rec["attempts"].append("dry-run: no call made")
        (FINAL_DIR / f"selfstop_{pod}_final.json").write_text(json.dumps(rec, indent=1))
        _log(f"WOULD TERMINATE ({why}) [dry run]")
        return True
    ok = False
    if shutil.which("runpodctl") and pod != "unknown-pod":
        r = subprocess.run(["runpodctl", "remove", "pod", pod], capture_output=True, text=True, timeout=120)
        rec["attempts"].append({"runpodctl": r.returncode, "out": (r.stdout + r.stderr)[-300:]})
        ok = r.returncode == 0
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    for url in (f"https://api.runpod.io/v2/pods/{pod}", f"https://rest.runpod.io/v1/pods/{pod}"):
        if ok or not key or pod == "unknown-pod":
            break
        code = _http_delete(url, key)
        rec["attempts"].append({url: code})
        ok = code in (200, 204)
    rec["ok"] = ok
    try:
        (FINAL_DIR / f"selfstop_{pod}_final.json").write_text(json.dumps(rec, indent=1))
        os.sync()
    except OSError:
        pass
    _log(f"TERMINATE ({why}): {'ok' if ok else 'FAILED'} {rec['attempts']}")
    return ok


def daemon_loop(tick_s: float, dry_run: bool) -> None:
    _log(f"daemon up pid={os.getpid()} pod={_pod()} tick={tick_s}s dry_run={dry_run}")
    while True:
        c = _load()
        if not c:
            _log("config vanished; exiting")
            return
        why = decide(c, time.time())
        if why and terminate(why, dry_run):
            if dry_run:
                c["terminated"] = why
                _save(c)
                return
            time.sleep(600)                                           # a successful remove kills us before this ends
        time.sleep(tick_s)


def _daemonize(tick_s: float, dry_run: bool) -> int:
    pid = os.fork()
    if pid:
        return pid
    os.setsid()
    if os.fork():
        os._exit(0)
    sys.stdin.close()
    fd = os.open(str(LOGF), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    PIDFILE.write_text(str(os.getpid()))
    try:
        daemon_loop(tick_s, dry_run)
    finally:
        os._exit(0)


def _running() -> bool:
    try:
        os.kill(int(PIDFILE.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def cmd_start(a) -> None:
    dl = dt.datetime.fromisoformat(a.deadline_utc.replace("Z", "+00:00"))
    if dl.tzinfo is None:
        sys.exit("deadline must carry a timezone (UTC)")
    c = {"deadline_utc": dl.astimezone(dt.timezone.utc).isoformat(timespec="seconds"), "started": time.time(),
         "startup_min": a.startup_min, "stall_min": a.stall_min, "after_done_min": a.after_done_min,
         "watch": [], "done_file": None, "note": a.note, "time_scale": a.time_scale}
    old = _load()
    if old and _running():
        c["watch"], c["done_file"] = old.get("watch", []), old.get("done_file")   # restart keeps registrations
        _log(f"restart: replacing deadline {old.get('deadline_utc')} -> {c['deadline_utc']}")
        os.kill(int(PIDFILE.read_text()), 15)
    _save(_touch_activity(c, "start"))
    _log(f"START deadline {c['deadline_utc']} startup {a.startup_min} min stall {a.stall_min} min note={a.note!r}")
    if a.foreground:
        daemon_loop(a.tick_s, a.dry_run)
    else:
        _daemonize(a.tick_s, a.dry_run)


def cmd_watch(a) -> None:
    c = _load()
    if not c:
        sys.exit("selfstop not started on this pod: run `start --deadline-utc ...` first (same deadline as the Mac arm)")
    c["watch"] = sorted(set(c.get("watch", [])) | {str(Path(d).resolve()) for d in a.dir})
    if a.done_file:
        c["done_file"] = str(Path(a.done_file).resolve())
    if a.stall_min is not None:
        c["stall_min"] = a.stall_min
    _save(_touch_activity(c, "watch"))
    _log(f"WATCH {c['watch']} done_file={c['done_file']} stall={c['stall_min']} min")
    if not _running():
        print("WARNING: the selfstop daemon is not running on this pod", file=sys.stderr)
        sys.exit(3)


def cmd_hold(a) -> None:
    c = _load()
    if not c:
        sys.exit("selfstop not started")
    STATE.mkdir(parents=True, exist_ok=True)
    HOLD.touch()
    _save(_touch_activity(c, "hold"))
    _log(f"HOLD: stall check paused {HOLD_MIN:.0f} min (deadline {c['deadline_utc']} unchanged)")


def cmd_check(a) -> None:
    pod, key = _pod(), os.environ.get("RUNPOD_API_KEY", "").strip()
    rep = {"RUNPOD_POD_ID": pod if pod != "unknown-pod" else None, "runpodctl": shutil.which("runpodctl"),
           "RUNPOD_API_KEY_present": bool(key), "daemon_running": _running(), "config": _load() or None}
    if key and pod != "unknown-pod":
        rep["own_pod_read_v2"] = _http_get(f"https://api.runpod.io/v2/pods/{pod}", key)
        rep["own_pod_read_v1"] = _http_get(f"https://rest.runpod.io/v1/pods/{pod}", key)
    # a read proves the credential authenticates, not that it may terminate; the first pod's live test proves that
    usable = rep["RUNPOD_POD_ID"] and (rep["runpodctl"] or 200 in (rep.get("own_pod_read_v2"), rep.get("own_pod_read_v1")))
    rep["terminate_path"] = "plausible (unproven until the live test)" if usable else "NONE: only the Mac watchdog stops this pod"
    print(json.dumps(rep, indent=1, default=str))
    sys.exit(0 if usable and rep["daemon_running"] else 2)


def cmd_status(a) -> None:
    c = _load()
    print(json.dumps({"running": _running(), "hold": hold_active(time.time(), float(c.get("time_scale", 1.0))), "config": c,
                      "would_stop_now": decide(c, time.time()) if c else None}, indent=1))
    if LOGF.exists():
        print("".join(LOGF.read_text().splitlines(True)[-8:]))


def cmd_stop_now(a) -> None:
    sys.exit(0 if terminate(f"manual: {a.why}", False) else 1)


def cmd_plan(a) -> None:
    """Mac side: one deadline, printed three ways, so the arm and the pod-side stop cannot disagree."""
    dl = (_now() + dt.timedelta(hours=a.hours)).replace(microsecond=0)
    iso = dl.isoformat().replace("+00:00", "Z")
    inner = (f"python3 {SCRIPT_ON_VOLUME} start --deadline-utc {iso} --note {shlex.quote(a.note)} "
             f"|| echo selfstop-missing > /tmp/selfstop_missing; exec /start.sh")
    print(json.dumps({"deadline_utc": iso,
                      "docker_start_cmd": ["bash", "-c", inner],
                      "watchdog_arm_after_create": f"tools/runpod_watch/pod_watchdog.py arm <POD_ID> --hours {a.hours} "
                                                   f"--note {shlex.quote(a.note + ' (selfstop ' + iso + ')')}"}, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--deadline-utc", required=True)
    s.add_argument("--startup-min", type=float, default=45)
    s.add_argument("--stall-min", type=float, default=30)
    s.add_argument("--after-done-min", type=float, default=45)
    s.add_argument("--note", default="")
    s.add_argument("--tick-s", type=float, default=60)
    s.add_argument("--time-scale", type=float, default=1.0, help=argparse.SUPPRESS)   # tests: minutes -> seconds
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--foreground", action="store_true")
    s.set_defaults(func=cmd_start)
    s = sub.add_parser("watch")
    s.add_argument("--dir", action="append", required=True)
    s.add_argument("--done-file")
    s.add_argument("--stall-min", type=float)
    s.set_defaults(func=cmd_watch)
    sub.add_parser("hold").set_defaults(func=cmd_hold)
    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("status").set_defaults(func=cmd_status)
    s = sub.add_parser("stop-now")
    s.add_argument("--why", required=True)
    s.set_defaults(func=cmd_stop_now)
    s = sub.add_parser("plan")
    s.add_argument("--hours", type=float, required=True)
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_plan)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
