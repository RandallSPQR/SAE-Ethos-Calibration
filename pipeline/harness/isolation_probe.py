#!/usr/bin/env python3
"""Five-minute isolation probe for a box: which doors are open, and which confinement backend passes all
five canaries. Run FIRST on any new pod; T2's driver refuses to generate unless a backend passes.

  python -m harness.isolation_probe            # prints the report, exit 0 if a backend passed 5/5, else 2

Logs, so the record says which door was locked: seccomp mode of this process (/proc/self/status),
euid, whether `unshare -Urn true` works (user+net namespaces), whether `bwrap --unshare-all true` works,
then the canary results per backend (harness.confine.select)."""
import json
import os
import platform
import shutil
import subprocess
import sys

from . import confine


def _sh(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()[-300:]
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def doors():
    d = {"platform": platform.platform(), "machine": platform.machine(), "euid": os.geteuid()}
    try:
        st = open("/proc/self/status").read()
        d["seccomp_mode"] = next((l.split()[1] for l in st.splitlines() if l.startswith("Seccomp:")), None)
        d["no_new_privs"] = next((l.split()[1] for l in st.splitlines() if l.startswith("NoNewPrivs:")), None)
        d["cap_bnd"] = next((l.split()[1] for l in st.splitlines() if l.startswith("CapBnd:")), None)
    except OSError:
        d["seccomp_mode"] = None
    d["unshare_Urn"] = _sh(["unshare", "-Urn", "true"]) if shutil.which("unshare") else (127, "no unshare binary")
    d["bwrap_unshare_all"] = (_sh(["bwrap", "--unshare-all", "--ro-bind", "/", "/", "true"]) if shutil.which("bwrap")
                              else (127, "no bwrap binary"))
    d["bwrap_user_uid"] = (_sh(["bwrap", "--unshare-all", "--unshare-user", "--uid", "61000", "--ro-bind", "/", "/", "id", "-u"])
                           if shutil.which("bwrap") else (127, "no bwrap binary"))
    return d


def main():
    rep = {"doors": doors()}
    rep["select"] = confine.select(uds=os.environ.get("TARGET_UDS"))
    print(json.dumps(rep, indent=1))
    d, s = rep["doors"], rep["select"]
    print(f"\nseccomp_mode={d.get('seccomp_mode')} euid={d['euid']} unshare_Urn_rc={d['unshare_Urn'][0]} "
          f"bwrap_rc={d['bwrap_unshare_all'][0]} candidates={s['candidates']}")
    for b, t in s["tried"].items():
        print(f"  {b}: {'PASS' if t['ok'] else 'FAIL'} canaries={t['canaries']}")
    if s["ok"]:
        print(f"ISOLATION PROBE: OK backend={s['backend']} canaries={s['canaries']}"); sys.exit(0)
    print("ISOLATION PROBE: NO BACKEND PASSED 5/5. STOP: real episodes must not run on this box."); sys.exit(2)


if __name__ == "__main__":
    main()
