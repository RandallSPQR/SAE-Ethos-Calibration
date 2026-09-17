#!/usr/bin/env python3
"""Launch the real Arm-A harness INSIDE a bubblewrap namespace, so harness.isolation's strong path holds
mechanically (network namespace off, repo the only writable bind, worker marker set by this launcher).

  python -m harness.launch_isolated --probe                      # can this box isolate at all? (exit 0/2)
  python -m harness.launch_isolated --uds /workspace/vllm.sock -- --build ../scenarios/build_t2 --n 10

The model is reached through a unix domain socket (`vllm serve --uds`) bind-mounted into the namespace,
so the net namespace can stay OFF: nothing model-authored can reach the network, and the only way out is
the OpenAI-compatible socket. If bwrap is unavailable or the namespace cannot be created (container
without user namespaces), this REFUSES; it never falls back to self-attestation.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .isolation import bwrap_command, bwrap_available, WORKER_ENV_MARKER

ROOT = Path(__file__).resolve().parent.parent          # pipeline/
REPO = ROOT.parent                                      # repo root: pipeline/ + scenarios/


def _extra_binds(venv, uds):
    b = ["--ro-bind", "/etc", "/etc", "--ro-bind", "/bin", "/bin", "--ro-bind", "/sbin", "/sbin"]
    if venv:
        b += ["--ro-bind", str(venv), str(venv)]
    if uds:
        b += ["--bind", str(uds), str(uds)]
    return b


def _wrap(inner, venv, uds):
    cmd = bwrap_command(inner, REPO)
    i = cmd.index("--setenv")                            # insert extra binds before the setenv/chdir tail
    return cmd[:i] + _extra_binds(venv, uds) + cmd[i:]


def probe(venv, uds):
    if not bwrap_available():
        print("ISOLATION PROBE: bwrap not installed (apt-get install -y bubblewrap)"); return 2
    inner = [sys.executable, "-c",
             "import os,socket,sys\n"
             "assert os.environ.get('%s')=='1', 'marker missing'\n"
             "s=socket.socket(); s.settimeout(0.5)\n"
             "try:\n s.connect(('1.1.1.1',53)); print('network REACHABLE inside namespace'); sys.exit(3)\n"
             "except OSError: print('network off inside namespace: OK')\n"
             "print('uds visible:', os.path.exists(%r) if %r else 'n/a')" % (WORKER_ENV_MARKER, uds or "", uds or "")]
    r = subprocess.run(_wrap(inner, venv, uds), capture_output=True, text=True)
    print((r.stdout + r.stderr).strip())
    if r.returncode != 0:
        print(f"ISOLATION PROBE: FAILED (rc={r.returncode}); this box cannot create the namespace. STOP: do not "
              "run real episodes here; the isolation precondition is enforced, not declared.")
        return 2
    print("ISOLATION PROBE: OK"); return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--uds", default=os.environ.get("TARGET_UDS"), help="vLLM --uds socket path to bind into the namespace")
    ap.add_argument("--venv", default=os.environ.get("VIRTUAL_ENV") or str(Path(sys.executable).resolve().parent.parent))
    ap.add_argument("harness_args", nargs="*", help="arguments after -- go to harness.run_harness")
    a = ap.parse_args()
    if a.probe:
        sys.exit(probe(a.venv, a.uds))
    if probe(a.venv, a.uds) != 0:
        sys.exit(2)
    env = {k: v for k, v in os.environ.items()}
    inner = [sys.executable, "-m", "harness.run_harness", *a.harness_args]
    cmd = _wrap(inner, a.venv, a.uds)
    # the launcher sets the marker INSIDE the namespace (bwrap --setenv); TARGET_UDS tells TargetClient the route
    if a.uds:
        i = cmd.index("--setenv"); cmd = cmd[:i] + ["--setenv", "TARGET_UDS", a.uds] + cmd[i:]
    i = cmd.index("--chdir"); cmd[i + 1] = str(ROOT)     # run from pipeline/ so `-m harness.run_harness` resolves
    print("launching:", " ".join(cmd[:8]), "...", " ".join(inner[2:]))
    sys.exit(subprocess.call(cmd, env=env))


if __name__ == "__main__":
    main()
