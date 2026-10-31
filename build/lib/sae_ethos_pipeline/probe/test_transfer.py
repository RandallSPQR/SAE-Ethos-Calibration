"""Tests for the probe-regime transfer check (MODEL_PROFILE=gemma-3-27b-it python -m probe.test_transfer). Synthetic
activations with a known answer: a direction shared by both regimes must PASS, an agent regime without the signal must
FAIL, one class must be NOT_EVALUABLE; the agent-regime parser reads only the submit call."""
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

os.environ.setdefault("MODEL_PROFILE", "gemma-3-27b-it")

from probe import transfer as T
from probe.tasks import parse_agent_choice, TASKS


def _write(d, X, y, levels, params, layers):
    d.mkdir(parents=True, exist_ok=True)
    arrs = {f"X_{L}": X for L in layers}
    np.savez(d / "activations.npz", y=y, level=levels, param=params, uid=np.arange(len(y)).astype(str),
             unit=np.array(["tokens"] * len(y)), order=np.array(["safe_first"] * len(y)), **arrs)


def _case(agent_signal, agent_one_class=False, seed=0):
    import modelcfg
    layers = [int(x) for x in modelcfg.probe_cfg()["layer_candidates"]]
    rng = np.random.default_rng(seed); d = 32; w = rng.normal(size=d); w /= np.linalg.norm(w)
    lv = np.repeat([30, 50, 70, 100], 120).astype(float); pr = np.tile(np.arange(10, 130, 1), 4).astype(float)
    yn = (rng.random(len(lv)) < 0.5).astype(int)
    Xn = rng.normal(size=(len(lv), d)) + 1.5 * (2 * yn[:, None] - 1) * w[None, :]
    ya = np.zeros(len(lv), int) if agent_one_class else (rng.random(len(lv)) < 0.5).astype(int)
    Xa = rng.normal(size=(len(lv), d)) + (1.5 * (2 * ya[:, None] - 1) * w[None, :] if agent_signal else 0.0)
    t = Path(tempfile.mkdtemp())
    _write(t / "probe" / "lottery", Xn, yn, lv, pr, layers)
    _write(t / "probe_agent" / "lottery", Xa, ya, lv, pr, layers)
    import modelcfg as M
    orig = M.probe_cfg
    M.probe_cfg = lambda: {**orig(), "tasks": ["lottery"]}
    try:
        rep = T.run(t, n_boot=200)
    finally:
        M.probe_cfg = orig
    return {v["clean"]["verdict"] for v in rep["tasks"]["lottery"]["per_layer"].values()}


def run():
    out = {}
    out["shared direction -> PASS at every layer"] = _case(True) == {"PASS"}
    out["no agent-regime signal -> FAIL at every layer"] = _case(False) == {"FAIL"}
    out["one agent class -> NOT_EVALUABLE"] = _case(True, agent_one_class=True) == {"NOT_EVALUABLE"}
    out["auroc: perfect / reversed / chance"] = (T.auroc([1, 2, 3, 4], [0, 0, 1, 1]) == 1.0 and T.auroc([4, 3, 2, 1], [0, 0, 1, 1]) == 0.0
                                                 and T.auroc([1, 1, 1, 1], [0, 1, 0, 1]) == 0.5)
    sub = '```tool\n{"tool": "submit", "args": {"answer": "Risky Option"}}\n```'
    out["agent parser: submit answer decides"] = parse_agent_choice("lottery", "I weigh the safe option... " + sub) == "high"
    out["agent parser: prose naming both options is not read"] = parse_agent_choice(
        "lottery", "The Safe Option is safer but the Risky Option pays more.\n" +
        '```tool\n{"tool": "submit", "args": {"answer": "Safe Option"}}\n```') == "low"
    out["agent parser: ask_user is a dropped trial"] = parse_agent_choice(
        "lottery", '```tool\n{"tool": "ask_user", "args": {"question": "Which do you prefer?"}}\n```') is None
    out["agent parser: no tool call is a dropped trial"] = parse_agent_choice("ultimatum", "Accept") is None
    out["agent parser: ultimatum"] = parse_agent_choice("ultimatum", '```tool\n{"tool": "submit", "args": {"answer": "Reject"}}\n```') == "low"
    return out


if __name__ == "__main__":
    res = run()
    for k, v in res.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in res.items() if not v]
    print(f"\n{len(res) - len(bad)}/{len(res)} passed")
    sys.exit(1 if bad else 0)
