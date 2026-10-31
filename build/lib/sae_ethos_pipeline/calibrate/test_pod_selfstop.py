"""Tests for pod_selfstop (python -m calibrate.test_pod_selfstop). No network, no pod: decisions are checked with
controlled mtimes, then the real forked daemon runs in dry-run mode with minutes compressed to seconds."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _env(tmp):
    return {**os.environ, "SELFSTOP_DIR": str(tmp / "state"), "SELFSTOP_FINAL_DIR": str(tmp / "final"),
            "RUNPOD_POD_ID": "testpod", "RUNPOD_API_KEY": ""}


def _cli(tmp, *args):
    return subprocess.run([sys.executable, str(HERE / "pod_selfstop.py"), *args], env=_env(tmp),
                          capture_output=True, text=True, timeout=30)


def decisions():
    """decide() on a fixed clock: each rule fires, and only when it should."""
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        os.environ.update({k: v for k, v in _env(t).items() if k.startswith("SELFSTOP")})
        import importlib
        from calibrate import pod_selfstop as ps
        importlib.reload(ps)
        now = time.time()
        iso = lambda s: __import__("datetime").datetime.fromtimestamp(s, __import__("datetime").timezone.utc).isoformat()
        base = {"deadline_utc": iso(now + 3600 * 10), "started": now - 60, "last_call": now - 60, "startup_min": 45,
                "stall_min": 30, "after_done_min": 45, "watch": [], "done_file": None}
        out = {}
        out["fresh_pod_runs"] = ps.decide(base, now) is None
        out["deadline_fires"] = "deadline" in (ps.decide({**base, "deadline_utc": iso(now - 1)}, now) or "")
        idle = {**base, "started": now - 46 * 60, "last_call": now - 46 * 60}
        out["unregistered_idle_fires_at_45"] = "no driver registered" in (ps.decide(idle, now) or "")
        out["unregistered_44_min_runs"] = ps.decide({**idle, "started": now - 44 * 60, "last_call": now - 44 * 60}, now) is None
        run = t / "run"
        (run / "gen").mkdir(parents=True)
        f = run / "gen" / "a.jsonl"
        f.write_text("x")
        os.utime(f, (now - 10 * 60, now - 10 * 60))
        watched = {**base, "watch": [str(run)], "started": now - 5 * 3600, "last_call": now - 5 * 3600}
        out["progress_keeps_alive"] = ps.decide(watched, now) is None
        os.utime(f, (now - 31 * 60, now - 31 * 60))
        out["stall_fires_at_30"] = "stalled" in (ps.decide(watched, now) or "")
        out["per_phase_stall_respected"] = ps.decide({**watched, "stall_min": 90}, now) is None
        ps.STATE.mkdir(parents=True, exist_ok=True)
        ps.HOLD.touch()
        os.utime(ps.HOLD, (now - 20 * 60, now - 20 * 60))
        out["hold_pauses_stall"] = ps.decide(watched, now) is None
        out["hold_never_pauses_deadline"] = "deadline" in (ps.decide({**watched, "deadline_utc": iso(now - 1)}, now) or "")
        os.utime(ps.HOLD, (now - 61 * 60, now - 61 * 60))
        out["hold_expires_at_60"] = "stalled" in (ps.decide(watched, now) or "")
        ps.HOLD.unlink()
        done = t / "DONE"
        done.write_text("")
        os.utime(done, (now - 10 * 60, now - 10 * 60))
        os.utime(f, (now - 60, now - 60))
        out["copy_window_runs"] = ps.decide({**watched, "done_file": str(done)}, now) is None
        os.utime(done, (now - 46 * 60, now - 46 * 60))
        out["done_fires_after_window"] = "run done" in (ps.decide({**watched, "done_file": str(done)}, now) or "")
        return out


def daemon():
    """The real forked daemon, dry-run, one minute = one second."""
    out = {}
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        dl = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        r = _cli(t, "start", "--deadline-utc", dl, "--startup-min", "3", "--time-scale", str(1 / 60), "--tick-s", "0.3", "--dry-run")
        out["start_ok"] = r.returncode == 0
        time.sleep(1.0)
        out["alive_before_startup_limit"] = not (t / "final" / "selfstop_testpod_final.json").exists()
        time.sleep(3.5)
        fin = t / "final" / "selfstop_testpod_final.json"
        out["unregistered_pod_stopped"] = fin.exists() and "no driver registered" in json.loads(fin.read_text())["why"]
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        run = t / "run"
        run.mkdir()
        dl = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        _cli(t, "start", "--deadline-utc", dl, "--startup-min", "2", "--stall-min", "2", "--time-scale", str(1 / 60),
             "--tick-s", "0.3", "--dry-run")
        r = _cli(t, "watch", "--dir", str(run))
        out["watch_ok"] = r.returncode == 0
        fin = t / "final" / "selfstop_testpod_final.json"
        for i in range(8):                                        # 4 s of progress, past both limits
            (run / f"{i}.jsonl").write_text("x")
            time.sleep(0.5)
        out["progressing_run_kept"] = not fin.exists()
        time.sleep(3.0)
        out["stalled_run_stopped"] = fin.exists() and "stalled" in json.loads(fin.read_text())["why"]
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        dl = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2))
        _cli(t, "start", "--deadline-utc", dl, "--startup-min", "999", "--tick-s", "0.3", "--dry-run")
        _cli(t, "hold")
        time.sleep(3.5)
        fin = t / "final" / "selfstop_testpod_final.json"
        out["deadline_beats_hold"] = fin.exists() and "deadline" in json.loads(fin.read_text())["why"]
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        r = _cli(t, "check")
        rep = json.loads(r.stdout)
        out["check_reports_no_path_without_creds"] = r.returncode == 2 and rep["terminate_path"].startswith("NONE")
        out["watch_refused_before_start"] = _cli(t, "watch", "--dir", str(t)).returncode != 0
        p = _cli(t, "plan", "--hours", "8", "--note", "27B burst 1")
        plan = json.loads(p.stdout)
        out["plan_one_deadline"] = plan["deadline_utc"] in plan["docker_start_cmd"][2] and "--hours 8" in plan["watchdog_arm_after_create"] \
            and plan["docker_start_cmd"][2].endswith("exec /start.sh")
    return out


if __name__ == "__main__":
    res = {**decisions(), **daemon()}
    for k, v in res.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in res.items() if not v]
    print(f"\n{len(res) - len(bad)}/{len(res)} passed")
    sys.exit(1 if bad else 0)
