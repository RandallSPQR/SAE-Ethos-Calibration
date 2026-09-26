#!/usr/bin/env python3
"""pod_watchdog.py: a launchd job on the Mac that terminates RunPod pods past their deadline, independent of
any Claude session or terminal. Two idle pods have billed for nothing (2026-09-18 ~3 h; 2026-09-25 ~20 h,
$32) because the session that created them stopped before the run finished. This job runs every 5 minutes
from launchd, which survives an idle session, a closed app and a laptop sleep (it fires on wake).

  pod_watchdog.py install                 # launchd job, every 300 s
  pod_watchdog.py arm <pod_id> --hours H  # register a deadline (now + H); re-arm to extend
  pod_watchdog.py disarm <pod_id>         # forget it (after you terminated it yourself)
  pod_watchdog.py tick                    # one pass (what launchd runs)
  pod_watchdog.py status

Rules, per tick, over every pod on the account:
  1. a registered pod past its deadline is TERMINATED;
  2. an UNREGISTERED pod that has been up longer than --max-unregistered-hours (default 3) is TERMINATED:
     a pod nobody armed is a pod nobody is watching;
  3. everything is logged to ~/runpod_watch/watchdog.log, and every termination pushes a macOS notification.
The API key comes from ~/runpod_watch/api_key (mode 600), $RUNPOD_API_KEY, or the keychain item, like runpod_watch.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import plistlib
import subprocess
import sys
import urllib.request
from pathlib import Path

DATA_DIR = Path(os.environ.get("RUNPOD_WATCH_DIR", str(Path.home() / "runpod_watch")))
DEADLINES = DATA_DIR / "pod_deadlines.json"
LOG = DATA_DIR / "watchdog.log"
LABEL = "com.sae-ethos.runpod-watchdog"
API = "https://api.runpod.io/v2/pods"
UA = "runpod-watch/1.0 (watchdog)"


def _key() -> str:
    k = os.environ.get("RUNPOD_API_KEY", "").strip()
    f = DATA_DIR / "api_key"
    if not k and f.exists():
        k = f.read_text().strip()
    if not k:
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", "RUNPOD_API_KEY", "-w"],
                               capture_output=True, text=True, timeout=30)
            k = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            k = ""
    if not k:
        sys.exit("no RunPod API key (see runpod_watch.py)")
    return k


def _req(method: str, url: str) -> tuple[int, dict | list | None]:
    req = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {_key()}", "User-Agent": UA,
                                                             "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _log(msg: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{_now().isoformat(timespec='seconds')} {msg}"
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line)


def _notify(text: str) -> None:
    try:
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "RunPod watchdog"'],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _load() -> dict:
    return json.loads(DEADLINES.read_text()) if DEADLINES.exists() else {}


def _save(d: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DEADLINES.write_text(json.dumps(d, indent=1))


def list_pods() -> list[dict]:
    code, body = _req("GET", API)
    if code != 200 or body is None:
        _log(f"list pods failed: HTTP {code}")
        return []
    return body.get("pods", body) if isinstance(body, dict) else body


def terminate(pod_id: str, why: str) -> bool:
    code, _ = _req("DELETE", f"{API}/{pod_id}")
    ok = code in (200, 204)
    _log(f"TERMINATE {pod_id} ({why}): HTTP {code}")
    _notify(f"terminated {pod_id}: {why}" if ok else f"FAILED to terminate {pod_id} (HTTP {code})")
    return ok


def cmd_tick(args) -> None:
    deadlines = _load()
    pods = list_pods()
    now = _now()
    for p in pods:
        pid = p.get("id")
        created = p.get("createdAt") or p.get("startedAt")
        try:
            age_h = (now - dt.datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds() / 3600
        except Exception:  # noqa: BLE001
            age_h = None
        if pid in deadlines:
            dl = dt.datetime.fromisoformat(deadlines[pid]["deadline"])
            if now >= dl:
                if terminate(pid, f"deadline {dl.isoformat(timespec='minutes')} passed"):
                    deadlines.pop(pid, None)
            else:
                _log(f"ok {pid} {p.get('name')} age={age_h and round(age_h, 2)}h deadline in {(dl - now).total_seconds() / 60:.0f} min")
        else:
            if age_h is not None and age_h > args.max_unregistered_hours:
                terminate(pid, f"unregistered pod up {age_h:.1f} h > {args.max_unregistered_hours} h")
            else:
                _log(f"UNREGISTERED {pid} {p.get('name')} age={age_h and round(age_h, 2)}h (killed at {args.max_unregistered_hours} h unless armed)")
    # forget deadlines for pods that no longer exist
    live = {p.get("id") for p in pods}
    for pid in [k for k in deadlines if k not in live]:
        _log(f"forget {pid}: no longer exists")
        deadlines.pop(pid)
    _save(deadlines)
    if not pods:
        _log("no pods")


def cmd_arm(args) -> None:
    d = _load()
    dl = _now() + dt.timedelta(hours=args.hours)
    d[args.pod_id] = {"deadline": dl.isoformat(timespec="seconds"), "armed": _now().isoformat(timespec="seconds"),
                      "note": args.note or ""}
    _save(d)
    _log(f"ARM {args.pod_id}: terminate at {dl.isoformat(timespec='minutes')} ({args.hours} h) {args.note or ''}")


def cmd_disarm(args) -> None:
    d = _load(); d.pop(args.pod_id, None); _save(d); _log(f"DISARM {args.pod_id}")


def cmd_status(args) -> None:
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
    print("launchd job:", "installed" if r.returncode == 0 else "NOT installed")
    print("deadlines:", json.dumps(_load(), indent=1))
    for p in list_pods():
        print("pod:", p.get("id"), p.get("name"), p.get("createdAt"), p.get("cost"))
    if LOG.exists():
        print("log tail:"); print("".join(LOG.read_text().splitlines(True)[-6:]))


def cmd_install(args) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app = DATA_DIR / "app"; app.mkdir(exist_ok=True)
    import shutil
    shutil.copy2(Path(__file__).resolve(), app / "pod_watchdog.py")       # TCC: agents cannot read ~/Documents
    plist = {"Label": LABEL, "ProgramArguments": [sys.executable, str(app / "pod_watchdog.py"), "tick",
                                                  "--max-unregistered-hours", str(args.max_unregistered_hours)],
             "StartInterval": 300, "RunAtLoad": True,
             "StandardOutPath": str(DATA_DIR / "watchdog.out"), "StandardErrorPath": str(DATA_DIR / "watchdog.err"),
             "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "RUNPOD_WATCH_DIR": str(DATA_DIR)}}
    p = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    with open(p, "wb") as fh:
        plistlib.dump(plist, fh)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(p)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"launchctl bootstrap failed: {r.stderr.strip()}")
    _log(f"installed {LABEL}: tick every 300 s, unregistered pods killed after {args.max_unregistered_hours} h")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("install"); s.add_argument("--max-unregistered-hours", type=float, default=3.0); s.set_defaults(func=cmd_install)
    s = sub.add_parser("arm"); s.add_argument("pod_id"); s.add_argument("--hours", type=float, required=True); s.add_argument("--note", default=""); s.set_defaults(func=cmd_arm)
    s = sub.add_parser("disarm"); s.add_argument("pod_id"); s.set_defaults(func=cmd_disarm)
    s = sub.add_parser("tick"); s.add_argument("--max-unregistered-hours", type=float, default=3.0); s.set_defaults(func=cmd_tick)
    s = sub.add_parser("status"); s.set_defaults(func=cmd_status)
    a = ap.parse_args(); a.func(a)


if __name__ == "__main__":
    main()
