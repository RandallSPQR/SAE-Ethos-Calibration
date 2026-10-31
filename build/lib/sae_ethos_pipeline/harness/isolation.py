"""Execution-isolation precondition. A real Arm-A episode runs untrusted model-authored code (pytest
executes files the model edited; bash runs model commands). For a TRUSTWORTHY experiment, isolation is
an ENFORCED precondition, not a deployment note: if the required guarantees aren't asserted, refuse to
run rather than silently producing results in an unsafe/uncontrolled environment.

The outer environment (container / bwrap / firejail) provides the guarantees; this module checks that
they were declared and, where cheaply checkable, verifies them. Set them in config/run.yaml:isolation
and provide them on the box (env markers), or pass allow_unsafe=True ONLY for mock/dev wiring runs.
"""
import os
import socket


REQUIRED = ["network_disabled", "isolated_mount", "no_host_credentials", "non_root_uid",
            "resource_limits", "process_timeout", "controlled_env"]


class IsolationError(RuntimeError):
    pass


def _network_really_off():
    """Cheap active check: a socket to a public IP should fail fast when the net namespace is down."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("1.1.1.1", 53))
        s.close()
        return False        # connected -> network is UP -> not isolated
    except OSError:
        return True


WORKER_ENV_MARKER = "ARM_A_ISOLATED_WORKER"   # the launcher sets this INSIDE the namespace it created


def bwrap_command(inner_cmd, repo_root):
    """Construct a rootless bubblewrap command that MECHANICALLY establishes the isolation properties,
    rather than trusting YAML: new net namespace (no egress), read-only /usr, repo bind, tmpfs elsewhere,
    no host creds, non-root, PID namespace. The launcher (not the operator) creates these, then runs the
    episode inside. If bwrap is unavailable, the caller must refuse — never fall back to self-attestation."""
    return [
        "bwrap", "--unshare-all", "--unshare-net",          # no network namespace at all
        "--die-with-parent", "--new-session",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--bind", str(repo_root), str(repo_root),           # only the repo is writable
        "--setenv", WORKER_ENV_MARKER, "1",
        "--chdir", str(repo_root),
        *inner_cmd,
    ]


def bwrap_available():
    import shutil
    return shutil.which("bwrap") is not None


def assert_isolated(cfg, allow_unsafe=False, confinement=None):
    """Raise IsolationError unless isolation is MECHANICALLY established. Preferred: we are running inside
    the launcher's namespace (WORKER_ENV_MARKER set) — the launcher created the guarantees, so they are
    not mere YAML claims. Fallback (declared in run.yaml) is accepted only with the cheap active checks,
    and is explicitly weaker. allow_unsafe bypasses for mock/dev and is recorded in the manifest."""
    if allow_unsafe:
        return {"isolated": False, "bypassed": True, "reason": "allow_unsafe (mock/dev only)"}
    # strongest path: a confinement backend whose canaries all held at launch (harness.confine.select)
    if confinement is not None:
        if confinement.get("ok"):
            return {"isolated": True, "mechanism": confinement["backend"], "canaries": confinement.get("canaries"),
                    "landlock_abi": confinement.get("landlock_abi"), "bpf_selftest": confinement.get("bpf_selftest"),
                    "tried": {b: t["canaries"] for b, t in confinement.get("tried", {}).items()}}
        raise IsolationError("Refusing to run a real episode: no confinement backend passed every canary "
                             f"(candidates {confinement.get('candidates')}; tried "
                             f"{ {b: t['canaries'] for b, t in confinement.get('tried', {}).items()} }). "
                             "Run `python -m harness.isolation_probe` to see which door is locked.")
    # strong path: inside the launched worker namespace
    if os.environ.get(WORKER_ENV_MARKER) == "1":
        if _network_really_off() is False:
            raise IsolationError("worker marker set but network reachable — namespace not effective.")
        return {"isolated": True, "mechanism": "bwrap_worker",
                "checks": {"euid": os.geteuid(), "network_off": _network_really_off()}}
    # weak path: operator declares isolation in run.yaml (kept for constrained environments)
    iso = (cfg or {}).get("isolation", {})
    missing = [k for k in REQUIRED if not iso.get(k)]
    if missing:
        raise IsolationError(
            "Refusing to run a real episode: not inside the isolated worker (preferred) and run.yaml "
            "isolation not fully declared: " + ", ".join(missing) + ". Launch via harness.isolation."
            "bwrap_command, or (weaker) declare isolation in run.yaml, or pass allow_unsafe for mocks.")
    if os.geteuid() == 0:
        raise IsolationError("Refusing to run as root (non_root_uid declared but euid==0).")
    if iso.get("network_disabled") and not _network_really_off():
        raise IsolationError("network_disabled declared but a network connection succeeded.")
    return {"isolated": True, "mechanism": "declared_yaml_weak",
            "checks": {"euid": os.geteuid(), "network_off": _network_really_off()}}
