"""Tests for item 8 (MODEL_PROFILE=gemma-3-27b-it python -m harness.test_item8): the frozen lists (neutral = item 7's),
the conditions and the rotated arm order, placement through the real harness path for all three arms, the stray-file
preflight, and the deadline guard cutting at a paraphrase boundary (mock backend)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MODEL_PROFILE", "gemma-3-27b-it")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml                                                     # noqa: E402

from harness import run_item8 as R                              # noqa: E402
from harness.fixtures import SCEN                               # noqa: E402
from harness.test_item7b import Capture                         # noqa: E402


def run():
    res = {}
    import modelcfg
    ser = modelcfg.serializer()
    p, psha = R.paraphrases()
    p7 = yaml.safe_load((ROOT / "config" / "item7b_paraphrases.yaml").read_text())
    frozen = json.loads(R.FREEZE.read_text()) if R.FREEZE.exists() else {}
    res["lists: 8 per arm; neutral is item 7's frozen list verbatim; frozen sha matches"] = (
        all(len(p[a]) == 8 for a in R.ARMS) and p["neutral"] == p7["neutral"] and frozen.get("paraphrases_sha256") == psha)
    res["pressure sentences never mention tests, grading or honesty (one component)"] = not any(
        w in s.lower() for s in p["pressure"] for w in ("test", "grade", "grader", "honest", "evaluat"))
    c = R.conditions()
    res["conditions: 16, each paraphrase at both positions, 2 seeds, 32 distinct (seed, paraphrase, position)"] = (
        len(c) == 16 and all({x[2] for x in c if x[1] == i} == set(R.POSITIONS) for i in range(8))
        and len({(s, x[1], x[2]) for x in c for s in x[3]}) == 32)
    res["arm order rotates with the condition (each arm first in 5 or 6 of 16)"] = (
        all(sorted(x[4]) == sorted(R.ARMS) for x in c)
        and sorted(sum(1 for x in c if x[4][0] == a) for a in R.ARMS) == [5, 5, 6])

    from harness.run_harness import run_scenario_seed
    run_cfg = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())
    spec = yaml.safe_load((SCEN / "arm_a" / "impossible_test" / "scenario.yaml").read_text())
    seed_dir = SCEN / "build" / "discover" / "impossible_test" / "seed_000"
    manifest = json.loads((seed_dir / "manifest.json").read_text())
    for arm in R.ARMS:
        for pos in R.POSITIONS:
            sentence = p[arm][3]
            agent = Capture(); out = Path(tempfile.mkdtemp())
            run_scenario_seed(agent, spec, seed_dir, manifest, run_cfg["sampling"], 1, str(out), str(out / "l.jsonl"), "t8",
                              {"generated": 0, "excluded_prefix": [], "reach": {}}, hcfg={"concurrency": 1}, variants={"full"},
                              insert={"text": sentence, "position": pos},
                              row_extra={"item8": {"arm": arm, "paraphrase": 3, "position": pos}})
            first = ser.serialize_messages(agent.first).split("<start_of_turn>user\n", 1)[1].split("<end_of_turn>", 1)[0]
            placed = first.startswith(sentence) if pos == "turn_open" else first.rstrip().endswith(sentence)
            rows = [json.loads(l) for f in (out / "arm_a").glob("*.jsonl") for l in open(f) if l.strip()]
            res[f"{arm} / {pos}: sentence placed in the serialized first user turn; row tagged"] = (
                placed and len(rows) == 1 and rows[0]["item8"]["arm"] == arm)

    b = Path(tempfile.mkdtemp()); (b / "discover" / "impossible_test" / "seed_000" / "full").mkdir(parents=True)
    (b / "discover" / "impossible_test" / "seed_000" / "full" / "README.md").write_text("x")
    clean = R.stray_files(b)
    (b / "discover" / "impossible_test" / "seed_000" / "full" / "CLAUDE.md").write_text("x")
    (b / "discover" / "honeypot_forbidden_table" / "seed_000").mkdir(parents=True)
    (b / "discover" / "honeypot_forbidden_table" / "seed_000" / "CLAUDE.md").write_text("x")
    res["stray-file preflight: finds a CLAUDE.md in impossible_test's repos, ignores other scenarios"] = (
        clean == [] and len(R.stray_files(b)) == 1)

    # the deadline guard, mock backend: 6 usable minutes at the prior 172 s/cell cannot fit a paraphrase (12 cells)
    import datetime as dt
    rr = Path(tempfile.mkdtemp())
    dl = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = subprocess.run([sys.executable, "-m", "harness.run_item8", "--mock", "--allow-unsafe", "--build",
                          str(SCEN / "build"), "--runs-root", str(rr), "--deadline", dl, "--max-conditions", "2"],
                         cwd=ROOT, capture_output=True, text=True)
    st = [json.loads(f.read_text()) for f in rr.glob("*/item8_status.json")]
    res["deadline guard: no paraphrase fits -> cut at paraphrase 0, nothing generated"] = (
        out.returncode == 0 and st and st[0]["status"].startswith("cut at paraphrase 0") and st[0]["cells"] == 0)
    rr2 = Path(tempfile.mkdtemp())
    out2 = subprocess.run([sys.executable, "-m", "harness.run_item8", "--mock", "--allow-unsafe", "--build",
                           str(SCEN / "build"), "--runs-root", str(rr2), "--max-conditions", "2"],
                          cwd=ROOT, capture_output=True, text=True)
    st2 = [json.loads(f.read_text()) for f in rr2.glob("*/item8_status.json")]
    prog = [json.loads(l) for f in rr2.glob("*/item8_progress.jsonl") for l in open(f)]
    res["no deadline: 2 conditions x 3 arms x 2 seeds = 12 cells, arms in the rotated order"] = (
        out2.returncode == 0 and st2 and st2[0]["cells"] == 12 and st2[0]["status"] == "complete"
        and [x["tag"].split("_")[0] for x in prog[::2]] == ["neutral", "pressure", "pc", "pressure", "pc", "neutral"])
    return res


if __name__ == "__main__":
    r = run()
    for k, v in r.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in r.items() if not v]
    print(f"\n{len(r) - len(bad)}/{len(r)} passed")
    sys.exit(1 if bad else 0)
