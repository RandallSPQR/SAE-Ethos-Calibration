#!/usr/bin/env python3
"""llama-probe-pods: a project's ONLY way to create, watch and terminate RunPod pods. Stdlib only.

Scoped to one project (several projects and sessions may share one RunPod account):
  1. `create` names the pod <prefix>..., bakes a pod-side self-stop into the start command, and writes the pod into THIS
     project's registry in the same process (no window in which a pod exists unwatched; if the registry write fails,
     the pod is deleted before the command returns);
  2. it watches, bills and terminates ONLY pods in that registry. It never lists the account's pods: a pod it did not
     create is never seen, never counted and never touched. `_terminate` refuses any id not in the registry, and any
     live pod whose name does not start with the prefix;
  3. it keeps a spend ledger over its own pods only. The kill switch compares (ledger + every live pod run to its
     deadline + the new pod run to its deadline) against the ceiling before a create, and the ledger alone at every
     tick; at the ceiling it terminates the project's live pods and locks further creates.
Stops per pod: the launchd tick (deadline + ceiling), the pod-side self-stop (deadline, and idle_min after boot if no
driver ever touched DRIVEN_FILE; pod/common.sh touches it), and any account-wide watchdog you run.

  llama-probe-pods --config pods.toml install            launchd job, one tick every 300 s, run through direnv
  llama-probe-pods --config pods.toml create --name NAME --hours H [--cloud SECURE|COMMUNITY] [--disk GB]
                                             [--secret-env VAR=runpod_secret_name ...] [--dry-run]
  llama-probe-pods --config pods.toml tick | status | terminate POD_ID [--why TEXT] | ssh-info POD_ID

API key: $RUNPOD_API_KEY from the environment ONLY (no key file is read). Set it once with
`newkey <project> RUNPOD_API_KEY` (Keychain item + the project's .envrc) and run commands as
`direnv exec . llama-probe-pods ...`. The launchd tick runs `direnv exec <project_root> <python> pods.py tick`, so it
loads the key the same way. The account key never goes on a pod: the self-stop uses RunPod's own injected, pod-scoped
RUNPOD_API_KEY, and `create` refuses if the account key appears in the pod body or the decoded self-stop.

Config (TOML or JSON):
  project = "my-study"          required; names the launchd label and the default prefix "<project>-"
  project_root = "~/code/my-study"   default: the config file's directory (holds the .envrc direnv loads)
  registry_dir = "~/runpod_watch_my-study"   default ~/runpod_watch_<project>
  ceiling_usd = 50.0   max_hours = 2.5   idle_min = 45   gpu = "NVIDIA RTX A6000"
  image = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"   name_prefix = "<project>-"
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import plistlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.runpod.io/v2/pods"
GRAPHQL = "https://api.runpod.io/graphql"
UA = "llama-probe-pods/1.0"                          # Cloudflare 403s the default Python UA
DRIVEN_FILE = "/tmp/pod_driven"                      # a driver touches it (pod/common.sh); absent -> idle self-stop
SELFSTOP = "/tmp/pod_selfstop.sh"
SELFSTOP_LOG = "/tmp/pod_selfstop.log"
DEFAULTS = {"ceiling_usd": 50.0, "max_hours": 2.5, "idle_min": 45, "gpu": "NVIDIA RTX A6000",
            "image": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"}

CFG: dict = {}


def load_config(path) -> dict:
    p = Path(path).expanduser().resolve()
    if p.suffix == ".toml":
        import tomllib
        raw = tomllib.loads(p.read_text())
    else:
        raw = json.loads(p.read_text())
    if not raw.get("project"):
        sys.exit(f"{p}: `project` is required")
    c = {**DEFAULTS, **raw}
    c["project_root"] = str(Path(raw.get("project_root") or p.parent).expanduser().resolve())
    c["registry_dir"] = str(Path(raw.get("registry_dir") or f"~/runpod_watch_{raw['project']}").expanduser())
    c.setdefault("name_prefix", f"{raw['project']}-")
    c["label"] = f"com.llama-probe.{raw['project']}.pods"
    c["ceiling_usd"], c["max_hours"], c["idle_min"] = float(c["ceiling_usd"]), float(c["max_hours"]), int(c["idle_min"])
    return c


def _dir() -> Path:
    return Path(CFG["registry_dir"])


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat()


def _key() -> str:
    k = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not k:
        sys.exit(f"RUNPOD_API_KEY is not set. Set it with `newkey {CFG.get('project', '<project>')} RUNPOD_API_KEY` "
                 f"and run as `direnv exec . llama-probe-pods ...` (no key file is read)")
    return k


def _req(method: str, url: str, body: dict | None = None) -> tuple[int, dict | list | str | None]:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {_key()}", "User-Agent": UA,
                                          "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)[:400]


def _log(msg: str) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    line = f"{_iso(_now())} {msg}"
    with open(_dir() / "watchdog.log", "a") as f:
        f.write(line + "\n")
    print(line)


def _notify(text: str) -> None:
    try:
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{CFG["project"]} pods"'],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def load() -> dict:
    reg = _dir() / "pods.json"
    return json.loads(reg.read_text()) if reg.exists() else {"pods": {}, "locked": False}


def save(reg: dict) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    tmp = _dir() / "pods.tmp"
    tmp.write_text(json.dumps(reg, indent=1))
    os.replace(tmp, _dir() / "pods.json")            # atomic: a crash never leaves a half-written registry


# ---- ledger: this project's pods only ----------------------------------------------------------------------------------

def _hours(a: str, b: dt.datetime) -> float:
    return max(0.0, (b - dt.datetime.fromisoformat(a)).total_seconds() / 3600)


def pod_cost(p: dict, now: dt.datetime) -> float:
    if p["state"] == "ended":
        return p["cost_usd"]
    return p["cost_per_hr"] * _hours(p["created_utc"], now)


def spent(reg: dict, now: dt.datetime) -> float:
    return sum(pod_cost(p, now) for p in reg["pods"].values())


def committed(reg: dict, now: dt.datetime) -> float:
    """Ledger plus every live pod running to its deadline: the most this project can be billed without a new create."""
    extra = sum(p["cost_per_hr"] * max(0.0, (dt.datetime.fromisoformat(p["deadline_utc"]) - now).total_seconds() / 3600)
                for p in reg["pods"].values() if p["state"] == "live")
    return spent(reg, now) + extra


def _end(reg: dict, pod_id: str, now: dt.datetime, why: str) -> None:
    p = reg["pods"][pod_id]
    p["cost_usd"] = round(pod_cost(p, now), 4)
    p.update(state="ended", ended_utc=_iso(now), end_reason=why)


# ---- the only terminate path ------------------------------------------------------------------------------------------

def _terminate(reg: dict, pod_id: str, why: str, live_name: str | None = None) -> bool:
    prefix = CFG["name_prefix"]
    if pod_id not in reg["pods"]:
        _log(f"REFUSE terminate {pod_id}: not created by this project ({why})")
        return False
    name = live_name if live_name is not None else reg["pods"][pod_id]["name"]
    if not name.startswith(prefix):
        _log(f"REFUSE terminate {pod_id}: live name {name!r} lacks {prefix!r} ({why})")
        return False
    code, resp = _req("DELETE", f"{API}/{pod_id}")
    ok = code in (200, 204, 404)                     # 404: already gone
    _log(f"TERMINATE {pod_id} {name} ({why}): HTTP {code}")
    _notify(f"terminated {pod_id}: {why}" if ok else f"FAILED to terminate {pod_id} (HTTP {code})")
    if ok:
        _end(reg, pod_id, _now(), why)
    return ok


# ---- pod-side self-stop -----------------------------------------------------------------------------------------------

def selfstop_script(deadline: dt.datetime, idle_at: dt.datetime) -> str:
    """Runs on the pod from its start command, before SSH exists. Terminates the pod at the deadline, or at idle_at if
    no driver has touched DRIVEN_FILE. RunPod injects RUNPOD_POD_ID and a pod-scoped RUNPOD_API_KEY into PID 1's
    environment, which this inherits."""
    d, i = int(deadline.timestamp()), int(idle_at.timestamp())
    return f"""#!/bin/bash
L={SELFSTOP_LOG}; echo "armed $(date -u +%FT%TZ) deadline={d} idle_at={i}" >> $L
while true; do
  now=$(date +%s)
  if [ "$now" -ge {d} ] || {{ [ "$now" -ge {i} ] && [ ! -e {DRIVEN_FILE} ]; }}; then
    echo "selfstop $(date -u +%FT%TZ) now=$now" >> $L
    runpodctl remove pod "$RUNPOD_POD_ID" >> $L 2>&1 || \\
      curl -s -X DELETE -H "Authorization: Bearer $RUNPOD_API_KEY" "https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID" >> $L 2>&1
  fi
  sleep 60
done
"""


def start_cmd(deadline: dt.datetime, idle_at: dt.datetime) -> str:
    b64 = base64.b64encode(selfstop_script(deadline, idle_at).encode()).decode()
    return f"(echo {b64} | base64 -d > {SELFSTOP} && nohup bash {SELFSTOP} >/dev/null 2>&1) & exec /start.sh"


# ---- commands ---------------------------------------------------------------------------------------------------------

def gpu_price(gpu: str, cloud: str) -> float | None:
    q = {"query": f'{{ gpuTypes(input:{{id:"{gpu}"}}) {{ securePrice communityPrice }} }}'}
    code, resp = _req("POST", GRAPHQL, q)
    try:
        t = resp["data"]["gpuTypes"][0]
        return float(t["securePrice"] if cloud == "SECURE" else t["communityPrice"])
    except Exception:  # noqa: BLE001
        return None


def cmd_create(a) -> int:
    reg, now = load(), _now()
    prefix, ceiling, max_h = CFG["name_prefix"], CFG["ceiling_usd"], CFG["max_hours"]
    if reg.get("locked"):
        sys.exit("REFUSED: the kill switch has locked creates (see status)")
    if not a.name.startswith(prefix):
        sys.exit(f"REFUSED: pod names must start with {prefix!r}")
    if not 0 < a.hours <= max_h:
        sys.exit(f"REFUSED: --hours must be in (0, {max_h}]")
    gpu = a.gpu or CFG["gpu"]
    price = gpu_price(gpu, a.cloud)
    if price is None:
        sys.exit("REFUSED: could not read the GPU price, so the ceiling cannot be checked")
    worst = committed(reg, now) + price * a.hours
    if worst > ceiling:
        sys.exit(f"REFUSED: committed ${committed(reg, now):.2f} + ${price:.2f}/h x {a.hours} h > ${ceiling:.0f}")
    deadline = now + dt.timedelta(hours=a.hours)
    idle_at = now + dt.timedelta(minutes=CFG["idle_min"])
    env = {}
    for s in a.secret_env:
        var, name = s.split("=", 1)
        env[var] = "{{ RUNPOD_SECRET_" + name + " }}"   # resolved by RunPod on the pod; the value never passes here
    body = {"name": a.name, "image": CFG["image"], "cloud": a.cloud, "gpu": {"id": gpu, "count": 1}, "disk": a.disk,
            "ports": ["22/tcp"], "startSsh": True, "entrypoint": ["bash", "-c"], "cmd": [start_cmd(deadline, idle_at)],
            "env": env}
    shipped = json.dumps(body) + selfstop_script(deadline, idle_at)   # body + decoded script
    if _key() in shipped:
        sys.exit("REFUSED: the account API key appears in the pod body")
    if a.dry_run:
        print(json.dumps(body, indent=1))
        print(f"\n--- {SELFSTOP}, decoded from cmd ---")
        print(selfstop_script(deadline, idle_at))
        print(f"price ${price:.2f}/h x {a.hours} h; worst-case committed after create ${worst:.2f} of ${ceiling:.0f}")
        return 0
    code, resp = _req("POST", API, body)
    if code not in (200, 201) or not isinstance(resp, dict) or not resp.get("id"):
        _log(f"create {a.name} failed: HTTP {code} {str(resp)[:200]}")
        return 2
    pid = resp["id"]
    try:
        reg["pods"][pid] = {"name": a.name, "created_utc": _iso(now), "deadline_utc": _iso(deadline),
                            "cost_per_hr": float(resp.get("costPerHr") or resp.get("cost") or price),
                            "state": "live", "gpu": gpu, "cloud": a.cloud, "note": a.note}
        save(reg)
    except Exception as e:  # noqa: BLE001
        code, _ = _req("DELETE", f"{API}/{pid}")
        _log(f"REGISTRY WRITE FAILED for {pid} ({e!r}); deleted it: HTTP {code}")
        return 3
    _log(f"CREATE {pid} {a.name} ${reg['pods'][pid]['cost_per_hr']:.2f}/h deadline {_iso(deadline)} "
         f"(self-stop baked in; worst-case committed ${committed(reg, now):.2f} of ${ceiling:.0f})")
    print(pid)
    return 0


def cmd_tick(a) -> int:
    reg, now = load(), _now()
    for pid, p in list(reg["pods"].items()):
        if p["state"] != "live":
            continue
        code, live = _req("GET", f"{API}/{pid}")       # one GET per registered pod; never a list of the account
        if code == 404:
            _end(reg, pid, now, "gone (self-stop, manual, or an account watchdog)")
            _log(f"ENDED {pid} {p['name']}: no longer exists; cost ${reg['pods'][pid]['cost_usd']:.2f}")
            continue
        if code != 200 or not isinstance(live, dict):
            _log(f"get {pid} failed: HTTP {code}")
            continue
        if live.get("costPerHr"):
            p["cost_per_hr"] = float(live["costPerHr"])
        if now >= dt.datetime.fromisoformat(p["deadline_utc"]):
            _terminate(reg, pid, f"deadline {p['deadline_utc']} passed", live_name=live.get("name", ""))
        else:
            mins = (dt.datetime.fromisoformat(p["deadline_utc"]) - now).total_seconds() / 60
            _log(f"ok {pid} {p['name']} ${pod_cost(p, now):.2f} so far, deadline in {mins:.0f} min")
    if spent(reg, now) >= CFG["ceiling_usd"]:
        reg["locked"] = True
        _log(f"KILL SWITCH: project spend ${spent(reg, now):.2f} >= ${CFG['ceiling_usd']:.0f}; terminating live pods, "
             f"locking creates")
        for pid, p in list(reg["pods"].items()):
            if p["state"] == "live":
                _terminate(reg, pid, "kill switch")
    save(reg)
    return 0


def cmd_terminate(a) -> int:
    reg = load()
    ok = _terminate(reg, a.pod_id, a.why)
    save(reg)
    return 0 if ok else 1


def cmd_ssh_info(a) -> int:
    """Public IP and the host port mapped to 22, for one of this project's pods (refuses any other id)."""
    reg = load()
    if a.pod_id not in reg["pods"]:
        sys.exit(f"REFUSED: {a.pod_id} was not created by this project")
    code, p = _req("GET", f"{API}/{a.pod_id}")
    if code != 200 or not isinstance(p, dict):
        sys.exit(f"get failed: HTTP {code}")
    d = ((p.get("ssh") or {}).get("direct") or {})                  # REST v2 shape
    ip, port = d.get("host"), d.get("port")
    print(json.dumps({"publicIp": ip, "ssh_port": port, "status": p.get("status"), "ready": bool(ip and port)}))
    return 0 if ip and port else 1


def cmd_status(a) -> int:
    reg, now = load(), _now()
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{CFG['label']}"], capture_output=True, text=True)
    print("launchd job:", "installed" if r.returncode == 0 else "NOT installed")
    print(f"spent ${spent(reg, now):.2f}; committed ${committed(reg, now):.2f} of ${CFG['ceiling_usd']:.0f}; "
          f"locked={reg.get('locked', False)}")
    for pid, p in reg["pods"].items():
        print(pid, p["name"], p["state"], p["deadline_utc"], f"${pod_cost(p, now):.2f}")
    return 0


def launchd_plist(cfg: dict, app: Path, direnv: str, python: str) -> dict:
    """The tick runs inside the project's direnv environment, which exports RUNPOD_API_KEY from Keychain (newkey)."""
    return {"Label": cfg["label"],
            "ProgramArguments": [direnv, "exec", cfg["project_root"], python, str(app / "pods.py"),
                                 "--config", str(app / "config.json"), "tick"],
            "StartInterval": 300, "RunAtLoad": True,
            "StandardOutPath": str(Path(cfg["registry_dir"]) / "watchdog.out"),
            "StandardErrorPath": str(Path(cfg["registry_dir"]) / "watchdog.err"),
            "EnvironmentVariables": {"PATH": f"{Path(direnv).parent}:/usr/bin:/bin:/usr/sbin:/sbin"}}


def cmd_install(a) -> int:
    direnv = shutil.which("direnv")
    if not direnv:
        sys.exit("direnv not found on PATH; the launchd tick loads RUNPOD_API_KEY through `direnv exec`")
    root = CFG["project_root"]
    if not (Path(root) / ".envrc").exists():
        sys.exit(f"{root}/.envrc missing; run `newkey {CFG['project']} RUNPOD_API_KEY` there first")
    chk = subprocess.run([direnv, "exec", root, "/bin/sh", "-c", 'test -n "$RUNPOD_API_KEY"'], capture_output=True)
    if chk.returncode:
        sys.exit(f"`direnv exec {root}` does not set RUNPOD_API_KEY; run `newkey {CFG['project']} RUNPOD_API_KEY` "
                 f"and `direnv allow` there")
    app = _dir() / "app"
    app.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), app / "pods.py")
    (app / "config.json").write_text(json.dumps({k: v for k, v in CFG.items() if k != "label"}, indent=1))
    plist = launchd_plist(CFG, app, direnv, sys.executable)
    p = Path.home() / "Library" / "LaunchAgents" / f"{CFG['label']}.plist"
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{CFG['label']}"], capture_output=True)
    with open(p, "wb") as fh:
        plistlib.dump(plist, fh)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(p)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"launchctl bootstrap failed: {r.stderr.strip()}")
    _log(f"installed {CFG['label']}: tick every 300 s over this project's registry only (direnv exec {root})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.environ.get("LLAMA_PROBE_PODS_CONFIG"), help="pods.toml / .json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("create")
    s.add_argument("--name", required=True); s.add_argument("--hours", type=float, required=True)
    s.add_argument("--gpu", default=None); s.add_argument("--cloud", default="SECURE", choices=["SECURE", "COMMUNITY"])
    s.add_argument("--disk", type=int, default=150); s.add_argument("--note", default="")
    s.add_argument("--secret-env", action="append", default=[], help="VAR=runpod_secret_name")
    s.add_argument("--dry-run", action="store_true", help="print the exact create body and self-stop script; create nothing")
    s.set_defaults(func=cmd_create)
    s = sub.add_parser("ssh-info"); s.add_argument("pod_id"); s.set_defaults(func=cmd_ssh_info)
    s = sub.add_parser("tick"); s.set_defaults(func=cmd_tick)
    s = sub.add_parser("terminate"); s.add_argument("pod_id"); s.add_argument("--why", default="manual")
    s.set_defaults(func=cmd_terminate)
    s = sub.add_parser("status"); s.set_defaults(func=cmd_status)
    s = sub.add_parser("install"); s.set_defaults(func=cmd_install)
    a = ap.parse_args(argv)
    if not a.config:
        sys.exit("--config (or $LLAMA_PROBE_PODS_CONFIG) is required")
    CFG.clear(); CFG.update(load_config(a.config))
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
