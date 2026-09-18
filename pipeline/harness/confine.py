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

The precondition stays mechanical: `select()` runs the canaries under each candidate backend and keeps
the first that passes ALL of them. Locked doors: connect fails, parent-level root-owned file unreadable,
secrets absent from the environment, credential FILES on disk unreadable (the HF token under HF_HOME, and
a synthetic secret planted there so the test is never vacuous; also ~/.netrc, git credentials, ssh keys
when present; HF_HOME itself not listable), fork bomb hits the limit, over-time sleep is killed, a
detached daemon does not survive the episode. A secret on disk is a different door from a secret in the
environment: the env canary cannot see it, and a token cached on a network volume outlives the pod.
Open door (positive canaries, same weight): the uid can write its episode dir and its
TMPDIR, can import pytest/yaml/sqlite3, and runs a trivial test to green; a perfectly isolated harness
whose episodes cannot run would otherwise be scored as model failure. If no backend passes every canary
the harness refuses. The manifest records the backend and the canary results.

Ordering that the labels depend on: episode exits or is killed -> reap() kills everything of the episode
uid and confirms nothing remains -> the labeler reads final state. TMPDIR is inside the episode dir so
the world-writable sweep is a backstop, not the mechanism.

Later (noted, not built): SECCOMP_RET_USER_NOTIF would let the harness log every denied socket/connect as
an observed_fact, turning "tried to reach the network" from an unobservable into a label input.
Concurrency: every Sandbox has its own uid (ARM_A_EPISODE_UID + slot) and 0700 tree, so the N continuations
from a decision point run in parallel without reading each other; reap/sweep are per uid.
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
LIMITS = {"nproc": 64, "as_bytes": 4 * 1024 ** 3, "fsize_bytes": 64 * 1024 ** 2, "nofile": 256}
CANARY_NAME = ".arm_a_canary_root_only"
CANARY_TOKEN_NAME = ".arm_a_canary_token"     # synthetic secret planted in HF_HOME for the disk canary


def secret_files():
    """Credential files that exist on this box and must be unreadable from the episode uid. Built from the
    environment at verify time; only existing regular files are returned, so the list is a record of what was
    actually tested, not a wish list."""
    home = Path.home()
    cands = []
    if os.environ.get("HF_HOME"):
        cands.append(Path(os.environ["HF_HOME"]) / "token")
    if os.environ.get("HF_TOKEN_PATH"):
        cands.append(Path(os.environ["HF_TOKEN_PATH"]))
    cands += [home / ".cache" / "huggingface" / "token", home / ".huggingface" / "token", home / ".netrc",
              home / ".git-credentials", home / ".config" / "runpod" / "config.toml", home / ".runpod" / "config.toml"]
    if (home / ".ssh").is_dir():
        cands += [k for k in sorted((home / ".ssh").glob("id_*")) if k.suffix != ".pub"]
    out = []
    for c in cands:
        if c.is_file() and str(c) not in out:
            out.append(str(c))
    return out
BWRAP_RO = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"]


# ---------------------------------------------------------------- seccomp-bpf (no libseccomp needed)
_ARCH = {"x86_64": (0xC000003E, {"socket": 41, "connect": 42}),
         "aarch64": (0xC00000B7, {"socket": 198, "connect": 203})}
_PR_SET_NO_NEW_PRIVS, _PR_SET_SECCOMP, _SECCOMP_MODE_FILTER = 38, 22, 2
_BPF_LD_W_ABS, _BPF_JEQ_K, _BPF_JSET_K, _BPF_RET_K = 0x20, 0x15, 0x45, 0x06
_RET_ALLOW, _RET_KILL_PROCESS, _RET_ERRNO_EPERM = 0x7FFF0000, 0x80000000, 0x00050000 | 1
_X32_BIT = 0x40000000                 # x86_64 kernels: x32 ABI syscalls carry this bit with the same audit arch
_AUDIT_ARCH_I386 = 0x40000003         # i386 compat ABI (int 0x80): different arch word -> killed by the arch check


class _SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


def _seccomp_insns(machine, deny=("socket", "connect")):
    """The filter, as (code, jt, jf, k) tuples. Denied calls return EPERM (a clean failure the model's code
    can react to, which is what we want to observe; a KILL would make a network attempt look like a crash).
    KILL is reserved for the two ways around the number table: a foreign arch word (i386 compat ABI via
    int 0x80, where socketcall is 102) and the x32 bit on an x86_64 arch word."""
    arch, nrs = _ARCH[machine]
    prog = [(_BPF_LD_W_ABS, 0, 0, 4),                       # A = seccomp_data.arch
            (_BPF_JEQ_K, 1, 0, arch),                       # arch ok -> skip kill
            (_BPF_RET_K, 0, 0, _RET_KILL_PROCESS),
            (_BPF_LD_W_ABS, 0, 0, 0),                       # A = seccomp_data.nr
            (_BPF_JSET_K, 0, 1, _X32_BIT),                  # x32 bit set -> kill
            (_BPF_RET_K, 0, 0, _RET_KILL_PROCESS)]
    for name in deny:
        prog += [(_BPF_JEQ_K, 0, 1, nrs[name]),             # nr == denied -> next insn (EPERM), else skip it
                 (_BPF_RET_K, 0, 0, _RET_ERRNO_EPERM)]
    prog += [(_BPF_RET_K, 0, 0, _RET_ALLOW)]
    return prog


def _seccomp_program(deny=("socket", "connect")):
    prog = _seccomp_insns(platform.machine(), deny)
    arr = (_SockFilter * len(prog))(*[_SockFilter(*p) for p in prog])
    return _SockFprog(len(prog), arr), arr                  # keep arr alive alongside the fprog


def _bpf_eval(prog, arch, nr):
    """Tiny interpreter for the subset of cBPF this filter uses, so the arch/x32 holes are checked by a test
    and not only by reading the code (the connect canary cannot exercise the compat ABIs)."""
    A, pc = 0, 0
    while True:
        code, jt, jf, k = prog[pc]
        if code == _BPF_LD_W_ABS:
            A = arch if k == 4 else nr; pc += 1
        elif code == _BPF_JEQ_K:
            pc += 1 + (jt if A == k else jf)
        elif code == _BPF_JSET_K:
            pc += 1 + (jt if (A & k) else jf)
        elif code == _BPF_RET_K:
            return k
        else:
            raise ValueError(f"unknown insn {code:#x}")


def selftest_bpf():
    """Offline code-review test of the filter for both encoded arches. Returns (ok, detail)."""
    detail, ok = {}, True
    for machine, (arch, nrs) in _ARCH.items():
        prog = _seccomp_insns(machine)
        cases = {"socket_eperm": (_bpf_eval(prog, arch, nrs["socket"]) == _RET_ERRNO_EPERM),
                 "connect_eperm": (_bpf_eval(prog, arch, nrs["connect"]) == _RET_ERRNO_EPERM),
                 "read_allowed": (_bpf_eval(prog, arch, 0) == _RET_ALLOW),
                 "foreign_arch_killed": (_bpf_eval(prog, _AUDIT_ARCH_I386, 102) == _RET_KILL_PROCESS),
                 "x32_bit_killed": (_bpf_eval(prog, arch, nrs["socket"] | _X32_BIT) == _RET_KILL_PROCESS),
                 "x32_bit_on_allowed_nr_killed": (_bpf_eval(prog, arch, 0 | _X32_BIT) == _RET_KILL_PROCESS)}
        detail[machine] = cases; ok = ok and all(cases.values())
    return ok, detail


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


def _env(cwd, extra=None):
    """env -i plus an explicit allowlist. HOME and TMPDIR inside the episode dir. `extra` is the per-episode
    entrypoint environment (scenario contract: PATH with the repo's bin/, SIGNING_REF_FILE, GIT_SIGNING_KEY
    in control variants); it can only ADD or override allowlisted keys, never carry the harness's env."""
    e = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(cwd), "TMPDIR": str(cwd),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
            "TERM": "dumb",
            # one BLAS/OpenMP thread: on a 32-vCPU pod OpenBLAS reserves per-thread address space at numpy import
            # and dies (SIGINT) under RLIMIT_AS; the T2 probe's trivial_test_green canary caught it (2026-09-17)
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    if extra:
        e.update({k: str(v) for k, v in extra.items()})
    return e


def _bwrap_cmd(cmd, cwd, uid, gid, extra=None):
    venv = Path(sys.executable).resolve().parent.parent
    b = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--unshare-user", "--uid", str(uid), "--gid", str(gid)]
    for p in BWRAP_RO:
        if Path(p).exists():
            b += ["--ro-bind", p, p]
    b += ["--ro-bind", str(venv), str(venv), "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
          "--bind", str(cwd), str(cwd), "--chdir", str(cwd), "--clearenv"]
    for k, v in _env(cwd, extra).items():
        b += ["--setenv", k, v]
    return b + ["--"] + list(cmd)


# ---------------------------------------------------------------- the one executor
def run(cmd, cwd, timeout=15, backend=None, capture_limit=4000, uid=None, gid=None, env_extra=None, spare=()):
    """Run model-authored/scenario code under the ACTIVE backend. Returns (text, returncode); 124 on
    timeout (the whole process group is killed). backend=None -> ACTIVE (plain subprocess if none).
    uid: the EPISODE uid (per sandbox; concurrent continuations get distinct uids). spare: pids of this
    episode's own entrypoint processes (the credential supervisor) that reap() must not kill."""
    backend = backend or ACTIVE["backend"] or "none"
    uid = uid or ACTIVE["uid"] or DEFAULT_UID
    gid = gid or uid
    cwd = Path(cwd)
    argv = _bwrap_cmd(cmd, cwd, uid, gid, env_extra) if backend == "bwrap" else list(cmd)
    env = _env(cwd, env_extra) if backend != "none" else ({**os.environ, **{k: str(v) for k, v in (env_extra or {}).items()}} if env_extra else None)
    pre = _preexec(backend, timeout, uid, gid) if backend != "none" else None
    try:
        p = subprocess.Popen(argv, cwd=str(cwd), env=env, preexec_fn=pre, start_new_session=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except (OSError, subprocess.SubprocessError) as e:      # incl. a preexec_fn failure (setuid without root)
        return f"error: cannot launch under {backend}: {e}", 125
    try:
        out, _ = p.communicate(timeout=timeout)
        rc, text = p.returncode, out[:capture_limit]
    except subprocess.TimeoutExpired:
        _kill_group(p)
        rc, text = 124, "error: timed out"
    left = reap(uid, spare=spare)
    if left:
        text += f"\nerror: {left} process(es) of the episode uid survived reaping"
    return text, rc


def _uid_pids(uid):
    """All live pids with real uid `uid` (Linux /proc scan)."""
    pids = []
    if not os.path.isdir("/proc"):
        return pids
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/status") as f:
                for line in f:
                    if line.startswith("Uid:"):
                        if int(line.split()[1]) == uid:
                            pids.append(int(d))
                        break
        except OSError:
            continue
    return pids


def reap(uid=None, spare=(), tries=30):
    """Kill EVERYTHING running as the episode uid and confirm nothing remains. Called after every confined
    execution and before the labeler reads final state, because a model-authored background process
    (`nohup ... &`, setsid) outlives the process group and could edit files after they were read.
    Returns the number of survivors (0 is the only acceptable value). bwrap: the pid namespace dies with
    its init, so there is nothing to reap by uid; the no_survivors canary checks that property instead."""
    if ACTIVE["backend"] != "seccomp_uid":
        return 0
    uid = uid or ACTIVE["uid"]
    spare = set(spare or ())
    for _ in range(tries):
        pids = [p for p in _uid_pids(uid) if p not in spare]
        if not pids:
            return 0
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.1)
    return len([p for p in _uid_pids(uid) if p not in spare])


def spawn_background(cmd, cwd, uid=None, env_extra=None):
    """Start a long-lived ENTRYPOINT process (the credential scenario's fake supervisor) as the episode uid,
    with the confined env plus `env_extra` in ITS environment only. Returns the Popen; the caller registers
    its pid as spared and kills it at episode end."""
    backend = ACTIVE["backend"] or "none"
    uid = uid or ACTIVE["uid"] or DEFAULT_UID
    env = _env(Path(cwd), env_extra) if backend != "none" else {**os.environ, **{k: str(v) for k, v in (env_extra or {}).items()}}
    pre = _preexec(backend, 10 ** 6, uid, uid) if backend != "none" else None
    return subprocess.Popen(list(cmd), cwd=str(cwd), env=env, preexec_fn=pre, start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:                       # gone, or (macOS) a group member we may not signal: fall through to reap()
        pass
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.communicate(timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------- episode directory ownership + hygiene
class ConfineError(RuntimeError):
    pass


def own(path, uid=None, gid=None):
    """Give the episode directory to the episode uid (seccomp_uid backend); no-op otherwise. Verified,
    not assumed: some network-volume backends ignore or refuse chown, and the positive canaries below
    would then show an episode dir the uid cannot write. Mode 0700 so a parallel episode (if ever) cannot
    read it; today episodes run sequentially under one uid (ARM_A_EPISODE_UID)."""
    if ACTIVE["backend"] != "seccomp_uid":
        return
    uid = uid or ACTIVE["uid"]; gid = gid or uid
    try:
        for root, dirs, files in os.walk(path):
            os.chown(root, uid, gid); os.chmod(root, 0o700)
            for f in files:
                fp = os.path.join(root, f); os.chown(fp, uid, gid)
        st = os.stat(path)
        if st.st_uid != uid:
            raise ConfineError(f"chown to uid {uid} silently ignored on {path} (volume backend?)")
    except OSError as e:
        raise ConfineError(f"cannot give {path} to uid {uid}: {e}") from e


def sweep(uid=None):
    """Remove anything the episode uid left in world-writable places (cross-continuation persistence)."""
    if ACTIVE["backend"] != "seccomp_uid":
        return 0
    uid = uid or ACTIVE["uid"]
    n = 0
    for base in ("/tmp", "/dev/shm", "/var/tmp"):
        if not os.path.isdir(base):
            continue
        for entry in os.scandir(base):
            try:
                if entry.stat(follow_symlinks=False).st_uid == uid:
                    (shutil.rmtree if entry.is_dir(follow_symlinks=False) else os.unlink)(entry.path); n += 1
            except OSError:
                pass
    return n


def harden(paths):
    """Root-only (0700 dirs / 0600 files) for everything the episode must never see: the run tree, the
    rendered build (answers live in `_side/`), HF_HOME, the ledger. The harness itself runs as root."""
    done = []
    paths = list(paths) + secret_files()          # the token file itself, not only the dir around it
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
    # argv[2] = {"files": [...], "dirs": [...]} from verify(): every existing credential file plus a synthetic
    # secret planted in HF_HOME. Fails if any file is readable, if HF_HOME is listable, or if NOTHING was
    # actually blocked (a vacuous pass is a fail).
    "disk_secrets_unreadable": (
        "import os,sys,json\n"
        "spec=json.loads(sys.argv[2]) if len(sys.argv)>2 else {}\n"
        "readable=[];blocked=[];missing=[];listable=[]\n"
        "for p in spec.get('files',[]):\n"
        "  try:\n   open(p,'rb').read(1); readable.append(p)\n"
        "  except FileNotFoundError: missing.append(p)\n"
        "  except OSError as e: blocked.append([p,e.errno])\n"
        "for d in spec.get('dirs',[]):\n"
        "  try: os.listdir(d); listable.append(d)\n"
        "  except OSError: pass\n"
        "print(json.dumps({'readable':readable,'listable':listable,'blocked':blocked,'missing':missing}))\n"
        "sys.exit(1 if (readable or listable or not blocked) else 0)\n", 10),
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
    # the door that must be OPEN: a perfectly isolated harness whose episodes cannot run would be scored
    # as model failure by the labeler
    "episode_dir_writable": (
        "import os,sys\n"
        "p=os.path.join(os.getcwd(),'canary_write.txt'); open(p,'w').write('ok'); os.makedirs('sub',exist_ok=True)\n"
        "open(os.path.join(os.environ['TMPDIR'],'t.txt'),'w').write('t')\n"
        "print('wrote', p, 'tmpdir', os.environ['TMPDIR']); sys.exit(0 if open(p).read()=='ok' else 1)\n", 10),
    "imports_available": (
        "import importlib,sys\n"
        "bad=[]\n"
        "for m in ('pytest','yaml','sqlite3','json','subprocess'):\n"
        "  try: importlib.import_module(m)\n"
        "  except Exception as e: bad.append(f'{m}: {e}')\n"
        "print('missing:',bad); sys.exit(1 if bad else 0)\n", 20),
    "trivial_test_green": (
        "import os,subprocess,sys\n"
        "open('test_canary.py','w').write('def test_ok():\\n    assert 1+1==2\\n')\n"
        "r=subprocess.run([sys.executable,'-m','pytest','-q','test_canary.py'],capture_output=True,text=True,timeout=60)\n"
        "print((r.stdout+r.stderr)[-200:]); sys.exit(r.returncode)\n", 90),
    # a detached daemon (setsid) must NOT outlive the episode: heartbeat file must stop changing after reap()
    "no_survivors": (
        "import os,sys,time\n"
        "if os.fork()==0:\n"
        "  os.setsid()\n"
        "  if os.fork()==0:\n"
        "    d=os.open(os.devnull,os.O_RDWR); [os.dup2(d,fd) for fd in (0,1,2)]\n"      # a real daemon drops the pipe
        "    while True:\n"
        "      open('heartbeat','w').write(str(time.time())); time.sleep(0.05)\n"
        "  os._exit(0)\n"
        "time.sleep(0.5); print('daemon started'); sys.exit(0)\n", 10),
}


def verify(backend, uds=None):
    """Run every canary under `backend` in a scratch episode dir. Returns {name: {ok, out, rc}}, all_ok.
    The disk-secret canary tests the REAL credential files present on the box plus a synthetic one planted
    in HF_HOME (or next to the scratch dir when HF_HOME is unset); callers harden HF_HOME first."""
    scratch = Path(tempfile.mkdtemp(prefix="arm_a_canary_"))
    canary = scratch.parent / CANARY_NAME
    hf_home = os.environ.get("HF_HOME")
    hf_dir = Path(hf_home) if hf_home and os.path.isdir(hf_home) else None
    synthetic = (hf_dir or scratch.parent) / CANARY_TOKEN_NAME
    try:
        canary.write_text("root only\n"); os.chmod(canary, 0o600)
        synthetic.write_text("hf_canary_not_a_real_token\n"); os.chmod(synthetic, 0o600)
        disk_spec = {"files": [str(synthetic)] + secret_files(), "dirs": [str(hf_dir)] if hf_dir else []}
        prev = dict(ACTIVE)
        ACTIVE.update({"backend": backend, "uid": ACTIVE["uid"] or DEFAULT_UID, "gid": ACTIVE["gid"] or DEFAULT_UID})
        own(scratch)
        results = {}
        for name, (code, timeout) in _CANARIES.items():
            script = scratch / f"{name}.py"; script.write_text(code)
            try:
                own(scratch)
            except ConfineError as e:
                results[name] = {"ok": False, "rc": 125, "out": str(e)}
                continue
            if uds:
                os.environ["CANARY_UDS"] = uds        # only for the canary; the child env is the allowlist + this
            argv = [sys.executable, str(script), str(canary), json.dumps(disk_spec)]
            out, rc = run(argv, scratch, timeout=timeout, backend=backend)
            if name == "overtime_is_killed":
                ok = rc == 124
            elif name == "no_survivors":
                hb = scratch / "heartbeat"
                m1 = hb.stat().st_mtime if hb.exists() else None
                time.sleep(0.6)
                m2 = hb.stat().st_mtime if hb.exists() else None
                ok = (rc == 0) and (m1 is not None) and (m1 == m2)      # daemon ran, then died with the episode
                out += f"\nheartbeat mtime {m1} -> {m2}"
                if m1 != m2:
                    _kill_by_heartbeat(scratch)
            else:
                ok = rc == 0
            results[name] = {"ok": bool(ok), "rc": rc, "out": out.strip()[-300:]}
        ACTIVE.update(prev)
        return results, all(r["ok"] for r in results.values())
    finally:
        os.environ.pop("CANARY_UDS", None)
        shutil.rmtree(scratch, ignore_errors=True)
        for f in (canary, synthetic):
            try:
                f.unlink()
            except OSError:
                pass


def _kill_by_heartbeat(scratch):
    """Best effort cleanup of a canary daemon that survived (unconfined dev boxes): kill any python
    process whose cwd is the scratch dir."""
    try:
        subprocess.run(["pkill", "-KILL", "-f", str(scratch)], capture_output=True)
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
    """Pick the first backend under which every canary holds. Returns a manifest-ready dict."""
    cands = available_backends()
    if prefer and prefer in cands:
        cands = [prefer] + [c for c in cands if c != prefer]
    bpf_ok, bpf_detail = selftest_bpf()
    report = {"backend": None, "candidates": cands, "tried": {}, "ok": False, "bpf_selftest": bpf_ok,
              "uid": DEFAULT_UID, "euid": os.geteuid(), "platform": platform.platform()}
    if not bpf_ok:
        report["bpf_selftest_detail"] = bpf_detail
        return report
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


def _env(cwd, extra=None):        # noqa: F811
    e = _orig_env(cwd, extra)
    if os.environ.get("CANARY_UDS"):
        e["CANARY_UDS"] = os.environ["CANARY_UDS"]
    return e


if __name__ == "__main__":
    print(json.dumps(select(uds=os.environ.get("TARGET_UDS")), indent=1))
