"""Scoping tests for pods.py against a FAKE RunPod API (ported from the trust study's tools/test_tg_pods.py, with the
constants now coming from a project config). No network, no pods, no key: every HTTP call is recorded, so the tests show
what the tool touched, not only what it returned. Added: the key comes from the environment only (a key file is never
read), and the launchd tick runs through `direnv exec <project_root>`."""
import datetime as dt
import json
import os
import subprocess

import pytest

from llama8b_it_base_probe import pods as P


class FakeAPI:
    def __init__(self, pods):
        self.pods = dict(pods)          # id -> {"name", "costPerHr"}
        self.calls = []
        self.next_id = "pnew000000001"

    def __call__(self, method, url, body=None):
        self.calls.append((method, url))
        if url.endswith("/graphql"):
            return 200, {"data": {"gpuTypes": [{"securePrice": 0.59, "communityPrice": 0.33}]}}
        if url.endswith("/v2/pods") and method == "GET":
            return 200, list(self.pods.values())
        if url.endswith("/v2/pods") and method == "POST":
            self.pods[self.next_id] = {"id": self.next_id, "name": body["name"], "costPerHr": 0.59}
            self.last_body = body
            return 201, {"id": self.next_id, "costPerHr": 0.59}
        pid = url.rsplit("/", 1)[1]
        if method == "GET":
            return (200, self.pods[pid]) if pid in self.pods else (404, None)
        if method == "DELETE":
            return (204, None) if self.pods.pop(pid, None) else (404, None)
        raise AssertionError((method, url))


FOREIGN = {"iocs0p96vytiju": {"id": "iocs0p96vytiju", "name": "item10-grader-2", "costPerHr": 1.79},
           "kwzw7yoclbtlq1": {"id": "kwzw7yoclbtlq1", "name": "tg-p0-gates", "costPerHr": 1.79}}
NEW = "pnew000000001"


@pytest.fixture
def pods(tmp_path, monkeypatch):
    cfg = tmp_path / "pods.toml"
    cfg.write_text(f'project = "demo"\nregistry_dir = "{tmp_path / "reg"}"\n')
    P.CFG.clear(); P.CFG.update(P.load_config(cfg))
    api = FakeAPI(FOREIGN)
    monkeypatch.setattr(P, "_req", api)
    monkeypatch.setattr(P, "_notify", lambda text: None)
    monkeypatch.setenv("RUNPOD_API_KEY", "rpa_TESTKEY")
    P.api = api
    return P


def args(**kw):
    base = dict(name="demo-run1", hours=2.0, gpu=None, cloud="SECURE", disk=150, note="",
                secret_env=["HF_TOKEN=hf_token"], why="manual", dry_run=False)
    base.update(kw)
    return type("A", (), base)()


def test_config_defaults_and_overrides(tmp_path):
    c = tmp_path / "pods.json"
    c.write_text(json.dumps({"project": "x", "ceiling_usd": 20, "gpu": "NVIDIA L40S"}))
    cfg = P.load_config(c)
    assert cfg["name_prefix"] == "x-" and cfg["ceiling_usd"] == 20.0 and cfg["gpu"] == "NVIDIA L40S"
    assert cfg["max_hours"] == 2.5 and cfg["project_root"] == str(tmp_path) and cfg["label"] == "com.llama-probe.x.pods"
    c.write_text(json.dumps({"ceiling_usd": 20}))
    with pytest.raises(SystemExit, match="project"):
        P.load_config(c)


def test_create_registers_in_the_same_call_with_selfstop_and_secret_reference(pods):
    assert pods.cmd_create(args()) == 0
    reg = pods.load()
    assert list(reg["pods"]) == [NEW] and reg["pods"][NEW]["state"] == "live"
    body = pods.api.last_body
    assert body["env"] == {"HF_TOKEN": "{{ RUNPOD_SECRET_hf_token }}"}          # a reference, never a value
    assert "pod_selfstop.sh" in body["cmd"][0] and body["cmd"][0].endswith("exec /start.sh")
    assert body["gpu"]["id"] == "NVIDIA RTX A6000" and body["image"] == P.DEFAULTS["image"]


def test_tick_touches_only_registered_pods(pods):
    pods.cmd_create(args())
    pods.api.calls.clear()
    pods.cmd_tick(None)
    assert ("GET", "https://api.runpod.io/v2/pods") not in pods.api.calls      # never lists the account
    assert {u.rsplit("/", 1)[1] for _, u in pods.api.calls} == {NEW}
    assert set(FOREIGN) <= set(pods.api.pods)


def test_terminate_refuses_foreign_pods(pods):
    for pid in FOREIGN:
        assert pods.cmd_terminate(args(pod_id=pid)) == 1
    assert not any(m == "DELETE" for m, _ in pods.api.calls)
    assert set(FOREIGN) <= set(pods.api.pods)


def test_terminate_refuses_registered_id_whose_live_name_is_not_ours(pods):
    pods.cmd_create(args())
    reg = pods.load()
    assert pods._terminate(reg, NEW, "test", live_name="someone-else") is False
    assert NEW in pods.api.pods


def test_deadline_terminates_own_pod_only(pods):
    pods.cmd_create(args())
    reg = pods.load()
    reg["pods"][NEW]["deadline_utc"] = (pods._now() - dt.timedelta(minutes=1)).isoformat()
    pods.save(reg)
    pods.cmd_tick(None)
    assert NEW not in pods.api.pods and set(FOREIGN) <= set(pods.api.pods)
    assert pods.load()["pods"][NEW]["state"] == "ended"


def test_ledger_counts_only_own_pods(pods):
    pods.cmd_create(args())
    reg = pods.load()
    reg["pods"][NEW]["created_utc"] = (pods._now() - dt.timedelta(hours=1)).isoformat()
    assert abs(pods.spent(reg, pods._now()) - 0.59) < 0.01                    # foreign pods' $1.79/h never counted


def test_create_refused_when_worst_case_exceeds_ceiling(pods):
    reg = pods.load()
    reg["pods"]["old"] = {"name": "demo-old", "created_utc": pods._iso(pods._now()), "deadline_utc": pods._iso(pods._now()),
                          "cost_per_hr": 0.59, "state": "ended", "cost_usd": 49.5}
    pods.save(reg)
    with pytest.raises(SystemExit, match="REFUSED"):
        pods.cmd_create(args(hours=1.0))
    assert not any(m == "POST" and u.endswith("/v2/pods") for m, u in pods.api.calls)


def test_kill_switch_terminates_own_live_pods_and_locks(pods):
    pods.cmd_create(args())
    reg = pods.load()
    reg["pods"]["old"] = {"name": "demo-old", "created_utc": pods._iso(pods._now()), "deadline_utc": pods._iso(pods._now()),
                          "cost_per_hr": 0.59, "state": "ended", "cost_usd": 50.0}
    pods.save(reg)
    pods.cmd_tick(None)
    reg = pods.load()
    assert reg["locked"] and reg["pods"][NEW]["state"] == "ended"
    assert set(FOREIGN) <= set(pods.api.pods)
    with pytest.raises(SystemExit, match="locked"):
        pods.cmd_create(args())


def test_registry_write_failure_deletes_the_new_pod(pods, monkeypatch):
    monkeypatch.setattr(pods, "save", lambda reg: (_ for _ in ()).throw(OSError("disk full")))
    assert pods.cmd_create(args()) == 3
    assert NEW not in pods.api.pods


def test_account_key_never_in_pod_body(pods, monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "rpa_ACCOUNTKEY_SHOULD_NEVER_LEAK")
    pods.cmd_create(args())
    body = json.dumps(pods.api.last_body)
    assert "rpa_ACCOUNTKEY" not in body and "$RUNPOD_API_KEY" in pods.selfstop_script(pods._now(), pods._now())
    monkeypatch.setattr(pods, "selfstop_script", lambda d, i: "curl -H 'Bearer rpa_ACCOUNTKEY_SHOULD_NEVER_LEAK'")
    with pytest.raises(SystemExit, match="account API key"):
        pods.cmd_create(args(name="demo-second"))


def test_rules_and_names(pods):
    with pytest.raises(SystemExit, match="demo-"):
        pods.cmd_create(args(name="p0"))
    with pytest.raises(SystemExit, match="hours"):
        pods.cmd_create(args(hours=3.0))


def test_key_from_environment_only(pods, tmp_path, monkeypatch):
    """A key file where the study's tool used to look is never read; an unset variable names the newkey command."""
    home = tmp_path / "home"; (home / "runpod_watch").mkdir(parents=True)
    (home / "runpod_watch" / "api_key").write_text("rpa_FILEKEY")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("RUNPOD_API_KEY")
    with pytest.raises(SystemExit, match="newkey demo RUNPOD_API_KEY"):
        P._key()
    monkeypatch.setenv("RUNPOD_API_KEY", " rpa_ENVKEY \n")
    assert P._key() == "rpa_ENVKEY"


def test_launchd_tick_runs_through_direnv(pods, tmp_path):
    app = tmp_path / "app"
    pl = P.launchd_plist(P.CFG, app, "/opt/homebrew/bin/direnv", "/venv/bin/python")
    assert pl["ProgramArguments"][:3] == ["/opt/homebrew/bin/direnv", "exec", P.CFG["project_root"]]
    assert pl["ProgramArguments"][3:] == ["/venv/bin/python", str(app / "pods.py"), "--config", str(app / "config.json"), "tick"]
    assert pl["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin:")
    assert "RUNPOD_API_KEY" not in json.dumps(pl) and pl["Label"] == "com.llama-probe.demo.pods"


def test_selfstop_script_is_valid_bash(pods, tmp_path):
    s = pods.selfstop_script(pods._now() + dt.timedelta(hours=2), pods._now() + dt.timedelta(minutes=45))
    f = tmp_path / "s.sh"; f.write_text(s)
    assert subprocess.run(["bash", "-n", str(f)]).returncode == 0


@pytest.mark.parametrize("case", ["deadline_passed", "never_driven", "driven_before_deadline"])
def test_selfstop_fires_when_it_should(pods, tmp_path, case):
    """Runs the real script for ~3 s with a stub runpodctl on PATH and records whether it called `remove pod`."""
    now = pods._now()
    deadline = now - dt.timedelta(minutes=1) if case == "deadline_passed" else now + dt.timedelta(hours=1)
    idle_at = now - dt.timedelta(minutes=1) if case != "deadline_passed" else now + dt.timedelta(hours=1)
    s = pods.selfstop_script(deadline, idle_at)
    if case == "driven_before_deadline":
        s = s.replace(P.DRIVEN_FILE, str(tmp_path / "driven"))
        (tmp_path / "driven").touch()
    else:
        s = s.replace(P.DRIVEN_FILE, str(tmp_path / "never"))
    s = s.replace(P.SELFSTOP_LOG, str(tmp_path / "log"))
    (tmp_path / "s.sh").write_text(s)
    stub = tmp_path / "runpodctl"
    stub.write_text(f'#!/bin/bash\necho "$*" > {tmp_path / "fired"}\n'); stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "RUNPOD_POD_ID": "ptest"}
    p = subprocess.Popen(["bash", str(tmp_path / "s.sh")], env=env)
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()
    fired = (tmp_path / "fired").read_text().strip() if (tmp_path / "fired").exists() else None
    assert fired == (None if case == "driven_before_deadline" else "remove pod ptest")
