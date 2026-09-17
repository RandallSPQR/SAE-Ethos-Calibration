"""Confinement of model-authored code (pytest, bash, git under the sandbox): the PROPERTIES the science
needs, with the MECHANISM pluggable and every property verified by a canary at launch.

Properties (what the untrusted child must not do):
  1. reach the network (fetch a solution, or anything at all; the vLLM endpoint is the harness's, not the
     sandbox's)
  2. read or write anything outside its episode directory (labels, snapshots, the harness's own state,
     the rendered `_side/` answers)
  3. read real credentials (the pod's HF token, RunPod variables)
  4. run away (processes, memory, file size, wall clock)
  5. persist anything across continuations

Backends:
  seccomp_uid  the child runs as a dedicated non-root uid with `env -i` + allowlist, rlimits, no_new_privs
               and a seccomp-bpf filter that denies socket/connect (unprivileged under no_new_privs, which is
               why this works in a Docker container where namespaces and iptables do not).
  bwrap        the child runs in a bubblewrap sandbox: new user/net/pid namespaces, only the episode
               directory bound, tmpfs /tmp, clear env, in-namespace uid; rlimits from the parent.
  none         mock/dev wiring only (fixtures, --mock); never a real episode.

The precondition stays mechanical: `select()` runs five canaries under each candidate backend and keeps
the first that fails ALL of them the way it must (connect fails, parent-level root-owned file unreadable,
secrets absent, fork bomb hits the limit, over-time sleep is killed). If no backend passes 5/5 the
harness refuses, exactly as before. The manifest records the backend and the canary results.
"""
import ctypes
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ACTIVE = {"backend": None, "uid": None, "gid": None}        # set by select(); read by run()
SECRET_KEYS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN", "LOCAL_API_KEY", "ANTHROPIC_API_KEY",
               "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY")
SECRET_PREFIXES = ("RUNPOD_", "HF_", "AWS_", "GITHUB_")
DEFAULT_UID = int(os.environ.get("ARM_A_EPISODE_UID", "61000"))
LIMITS = {"nproc": 64, "as_bytes": 2 * 1024 ** 3, "fsize_bytes": 64 * 1024 ** 2, "nofile": 256}
CANARY_NAME = ".arm_a_canary_root_only"
BWRAP_RO = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"]


# ---------------------------------------------------------------- seccomp-bpf (no libseccomp needed)
_ARCH = {"x86_64": (0xC000003E, {"socket": 41, "connect": 42}),
         "aarch64": (0xC00000B7, {"socket": 198, "connect": 203})}
_PR_SET_NO_NEW_PRIVS, _PR_SET_SECCOMP, _SECCOMP_MODE_FILTER = 38, 22, 2
_BPF_LD_W_ABS, _BPF_JEQ_K, _BPF_RET_K = 0x20, 0x15, 0x06
_RET_ALLOW, _RET_KILL_PROCESS, _RET_ERRNO_EPERM = 0x7FFF0000, 0x80000000, 0x00050000 | 1


class _SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


def _seccomp_program(deny=("socket", "connect")):
    arch, nrs = _ARCH[platform.machine()]
    prog = [(_BPF_LD_W_ABS, 0, 0, 4),                       # A = seccomp_data.arch
            (_BPF_JEQ_K, 1, 0, arch),                       # arch ok -> skip kill
            (_BPF_RET_K, 0, 0, _RET_KILL_PROCESS),
            (_BPF_LD_W_ABS, 0, 0, 0)]                       # A = seccomp_data.nr
    for name in deny:
        prog += [(_BPF_JEQ_K, 0, 1, nrs[name]),             # nr == denied -> next insn (EPERM), else skip it
                 (_BPF_RET_K, 0, 0, _RET_ERRNO_EPERM)]
    prog += [(_BPF_RET_K, 0, 0, _RET_ALLOW)]
    arr = (_SockFilter * len(prog))(*[_SockFilter(*p) for p in prog])
    return _SockFprog(len(prog), arr), arr                  # keep arr alive alongside the fprog


def install_seccomp():
    """In the child, after setuid and before exec. Filters survive execve."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
    fprog, _keep = _seccomp_program()
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_SECCOMP failed")


# ---------------------------------------------------------------- child setup
def _set_rlimits(timeout):
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (LIMITS["nofile"], LIMITS["nofile"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMITS["fsize_bytes"], LIMITS["fsize_bytes"]))
    resource.setrlimit(resource.RLIMIT_CPU, (int(timeout) + 5, int(timeout) + 10))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (LIMITS["nproc"], LIMITS["nproc"]))
    if platform.system() == "Linux":
        resource.setrlimit(resource.RLIMIT_AS, (LIMITS["as_bytes"], LIMITS["as_bytes"]))


def _preexec(backend, timeout, uid, gid):
    def fn():
        _set_rlimits(timeout)
        if backend == "seccomp_uid":
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            install_seccomp()
    return fn


def _env(cwd):
    """env -i plus an explicit allowlist. HOME and TMPDIR inside the episode dir."""
    return {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(cwd), "TMPDIR": str(cwd),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
            "TERM": "dumb"}


def _bwrap_cmd(cmd, cwd, uid, gid):
    venv = Path(sys.executable).resolve().parent.parent
    b = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--unshare-user", "--uid", str(uid), "--gid", str(gid)]
    for p in BWRAP_RO:
        if Path(p).exists():
            b += ["--ro-bind", p, p]
    b += ["--ro-bind", str(venv), str(venv), "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
          "--bind", str(cwd), str(cwd), "--chdir", str(cwd), "--clearenv"]
    for k, v in _env(cwd).items():
        b += ["--setenv", k, v]
    return b + ["--"] + list(cmd)


# ---------------------------------------------------------------- the one executor
def run(cmd, cwd, timeout=15, backend=None, capture_limit=4000):
    """Run model-authored/scenario code under the ACTIVE backend. Returns (text, returncode); 124 on
    timeout (the whole process group is killed). backend=None -> ACTIVE (plain subprocess if none)."""
    backend = backend or ACTIVE["backend"] or "none"
    uid, gid = ACTIVE["uid"] or DEFAULT_UID, ACTIVE["gid"] or DEFAULT_UID
    cwd = Path(cwd)
    argv = _bwrap_cmd(cmd, cwd, uid, gid) if backend == "bwrap" else list(cmd)
    env = _env(cwd) if backend != "none" else None
    pre = _preexec(backend, timeout, uid, gid) if backend != "none" else None
    try:
        p = subprocess.Popen(argv, cwd=str(cwd), env=env, preexec_fn=pre, start_new_session=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except (OSError, subprocess.SubprocessError) as e:      # incl. a preexec_fn failure (setuid without root)
        return f"error: cannot launch under {backend}: {e}", 125
    try:
        out, _ = p.communicate(timeout=timeout)
        return out[:capture_limit], p.returncode
    except subprocess.TimeoutExpired:
        _kill_group(p)
        return "error: timed out", 124


def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        p.communicate(timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------- episode directory ownership + hygiene
def own(path):
    """Give the episode directory to the episode uid (seccomp_uid backend); no-op otherwise."""
    if ACTIVE["backend"] != "seccomp_uid":
        return
    uid, gid = ACTIVE["uid"], ACTIVE["gid"]
    for root, dirs, files in os.walk(path):
        os.chown(root, uid, gid)
        for f in files:
            os.chown(os.path.join(root, f), uid, gid)


def sweep():
    """Remove anything the episode uid left in world-writable places (cross-continuation persistence)."""
    if ACTIVE["backend"] != "seccomp_uid":
        return 0
    n = 0
    for base in ("/tmp", "/dev/shm", "/var/tmp"):
        if not os.path.isdir(base):
            continue
        for entry in os.scandir(base):
            try:
                if entry.stat(follow_symlinks=False).st_uid == ACTIVE["uid"]:
                    (shutil.rmtree if entry.is_dir(follow_symlinks=False) else os.unlink)(entry.path); n += 1
            except OSError:
                pass
    return n


def harden(paths):
    """Root-only (0700 dirs / 0600 files) for everything the episode must never see: the run tree, the
    rendered build (answers live in `_side/`), HF_HOME, the ledger. The harness itself runs as root."""
    done = []
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        if os.path.isdir(p):
            os.chmod(p, 0o700)
        else:
            os.chmod(p, 0o600)
        done.append(str(p))
    return done


# ---------------------------------------------------------------- canaries
_CANARIES = {
    "network_connect_fails": (
        "import socket,sys,os\n"
        "try:\n s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); s.settimeout(2); s.connect(('1.1.1.1',53)); print('CONNECTED'); sys.exit(1)\n"
        "except OSError as e: print('blocked:',e)\n"
        "u=os.environ.get('CANARY_UDS')\n"
        "if u:\n"
        "  try:\n   s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(u); print('UDS CONNECTED'); sys.exit(1)\n"
        "  except OSError as e: print('uds blocked:',e)\n", 10),
    "parent_level_file_unreadable": (
        "import sys\n"
        "try:\n open(sys.argv[1]).read(); print('READ'); sys.exit(1)\n"
        "except OSError as e: print('blocked:',e)\n", 10),
    "secrets_absent": (
        "import os,json,sys\n"
        "keys=%r; pre=%r\n"
        "leak=[k for k in os.environ if k in keys or k.startswith(pre)]\n"
        "print(json.dumps(sorted(leak))); sys.exit(1 if leak else 0)\n" % (SECRET_KEYS, SECRET_PREFIXES), 10),
    "fork_bomb_hits_limit": (
        "import os,sys,time\n"
        "kids=[]\n"
        "for i in range(400):\n"
        "  try:\n   pid=os.fork()\n"
        "  except OSError as e:\n   print('limit at',i,e); [os.kill(k,9) for k in kids]; sys.exit(0)\n"
        "  if pid==0: time.sleep(20); os._exit(0)\n"
        "  kids.append(pid)\n"
        "print('NO LIMIT'); [os.kill(k,9) for k in kids]; sys.exit(1)\n", 20),
    "overtime_is_killed": ("import time\ntime.sleep(30)\nprint('SURVIVED')\n", 3),
}


def verify(backend, uds=None):
    """Run the five canaries under `backend` in a scratch episode dir. Returns {name: {ok, out, rc}}, all_ok."""
    scratch = Path(tempfile.mkdtemp(prefix="arm_a_canary_"))
    canary = scratch.parent / CANARY_NAME
    try:
        canary.write_text("root only\n"); os.chmod(canary, 0o600)
        prev = dict(ACTIVE)
        ACTIVE.update({"backend": backend, "uid": ACTIVE["uid"] or DEFAULT_UID, "gid": ACTIVE["gid"] or DEFAULT_UID})
        own(scratch)
        results = {}
        for name, (code, timeout) in _CANARIES.items():
            script = scratch / f"{name}.py"; script.write_text(code); own(scratch)
            if uds:
                os.environ["CANARY_UDS"] = uds        # only for the canary; the child env is the allowlist + this
            argv = [sys.executable, str(script), str(canary)]
            out, rc = run(argv, scratch, timeout=timeout, backend=backend)
            if name == "overtime_is_killed":
                ok = rc == 124
            else:
                ok = rc == 0
            results[name] = {"ok": bool(ok), "rc": rc, "out": out.strip()[-300:]}
        ACTIVE.update(prev)
        return results, all(r["ok"] for r in results.values())
    finally:
        os.environ.pop("CANARY_UDS", None)
        shutil.rmtree(scratch, ignore_errors=True)
        try:
            canary.unlink()
        except OSError:
            pass


def available_backends():
    """Candidate order: seccomp_uid needs Linux + root (to drop uid); bwrap needs the binary."""
    out = []
    if platform.system() == "Linux" and os.geteuid() == 0 and platform.machine() in _ARCH:
        out.append("seccomp_uid")
    if shutil.which("bwrap"):
        out.append("bwrap")
    return out


def select(prefer=None, uds=None):
    """Pick the first backend whose five canaries all hold. Returns a manifest-ready dict."""
    cands = available_backends()
    if prefer and prefer in cands:
        cands = [prefer] + [c for c in cands if c != prefer]
    report = {"backend": None, "candidates": cands, "tried": {}, "ok": False,
              "uid": DEFAULT_UID, "euid": os.geteuid(), "platform": platform.platform()}
    for b in cands:
        res, ok = verify(b, uds=uds)
        report["tried"][b] = {"ok": ok, "canaries": {k: v["ok"] for k, v in res.items()},
                              "detail": {k: v["out"] for k, v in res.items()}}
        if ok:
            report.update({"backend": b, "ok": True, "canaries": f"{sum(v['ok'] for v in res.values())}/{len(res)}"})
            ACTIVE.update({"backend": b, "uid": DEFAULT_UID, "gid": DEFAULT_UID})
            break
    return report


# the canary env: run() builds the allowlist; CANARY_UDS must pass through for the network canary only
_orig_env = _env


def _env(cwd):        # noqa: F811
    e = _orig_env(cwd)
    if os.environ.get("CANARY_UDS"):
        e["CANARY_UDS"] = os.environ["CANARY_UDS"]
    return e


if __name__ == "__main__":
    print(json.dumps(select(uds=os.environ.get("TARGET_UDS")), indent=1))
