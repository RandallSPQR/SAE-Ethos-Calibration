"""Tests for item 9 (MODEL_PROFILE=gemma-3-27b-it python -m harness.test_item9): the lists are the frozen item 7 / 8 lists
verbatim, the condition scheme, blocks and rotation, placement at turn_close through the real harness path for all four
arms, the two-lane mock run (separate lane directories, merged tally) and the deadline guard (mock backend)."""
import collections
import datetime as dt
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

from harness import run_item9 as R                              # noqa: E402
from harness.fixtures import SCEN                               # noqa: E402
from harness.test_item7b import Capture                         # noqa: E402


def run():
    res = {}
    import modelcfg
    ser = modelcfg.serializer()
    p, psha = R.paraphrases()
    p7 = yaml.safe_load((ROOT / "config" / "item7b_paraphrases.yaml").read_text())
    p8 = yaml.safe_load((ROOT / "config" / "item8_paraphrases.yaml").read_text())
    frozen = json.loads(R.FREEZE.read_text()) if R.FREEZE.exists() else {}
    res["lists verbatim: neutral / pc / pressure = item 8's, grader = item 7's; frozen sha matches"] = (
        p["neutral"] == p8["neutral"] == p7["neutral"] and p["pc"] == p8["pc"] and p["pressure"] == p8["pressure"]
        and p["grader"] == p7["grader"] and frozen.get("paraphrases_sha256") == psha)
    c = R.conditions()
    pairs = {(x[1], x[2]) for x in c}
    seeds_per_para = collections.Counter(x[2] for x in c); paras_per_seed = collections.Counter(x[1] for x in c)
    res["conditions: 80 distinct (seed, paraphrase) pairs, seeds 0-39 x 2 paraphrases, each paraphrase 10 seeds"] = (
        len(c) == 80 and len(pairs) == 80 and set(paras_per_seed) == set(range(40)) and set(paras_per_seed.values()) == {2}
        and set(seeds_per_para.values()) == {10})
    blocks = [c[k:k + R.BLOCK] for k in range(0, 80, R.BLOCK)]
    res["blocks of 8 conditions each hold every paraphrase once; each arm leads 20 of 80 conditions"] = (
        all(sorted(x[2] for x in b) == list(range(8)) for b in blocks)
        and all(n == 20 for n in collections.Counter(x[3][0] for x in c).values()))

    from harness.run_harness import run_scenario_seed
    run_cfg = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())
    spec = yaml.safe_load((SCEN / "arm_a" / "impossible_test" / "scenario.yaml").read_text())
    seed_dir = SCEN / "build" / "discover" / "impossible_test" / "seed_000"
    manifest = json.loads((seed_dir / "manifest.json").read_text())
    for arm in R.ARMS:
        sentence = p[arm][5]
        agent = Capture(); out = Path(tempfile.mkdtemp())
        run_scenario_seed(agent, spec, seed_dir, manifest, run_cfg["sampling"], 1, str(out), str(out / "l.jsonl"), "t9",
                          {"generated": 0, "excluded_prefix": [], "reach": {}}, hcfg={"concurrency": 1}, variants={"full"},
                          insert={"text": sentence, "position": R.POSITION}, row_extra={"item9": {"arm": arm}})
        first = ser.serialize_messages(agent.first).split("<start_of_turn>user\n", 1)[1].split("<end_of_turn>", 1)[0]
        res[f"{arm}: the sentence is the closing line of the serialized first user turn"] = (
            first.rstrip().endswith(sentence) and not first.startswith(sentence))

    rr = Path(tempfile.mkdtemp())
    o = subprocess.run([sys.executable, "-m", "harness.run_item9", "--mock", "--allow-unsafe", "--build", str(SCEN / "build"),
                        "--runs-root", str(rr), "--max-blocks", "1"], cwd=ROOT, capture_output=True, text=True)
    st = [json.loads(f.read_text()) for f in rr.glob("*/item9_status.json")]
    prog = [json.loads(l) for f in rr.glob("*/item9_progress.jsonl") for l in open(f)]
    lanes = sorted({x.name for x in rr.glob("*/generation/impossible_test/lane*")})
    res["mock, one block: 32 cells over 2 lanes (16 each), separate lane directories, status complete"] = (
        o.returncode == 0 and st and st[0]["cells"] == 32 and st[0]["status"] == "complete"
        and collections.Counter(x["lane"] for x in prog) == {0: 16, 1: 16} and lanes == ["lane0", "lane1"])
    res["mock: every (condition, arm) cell of the block ran exactly once"] = (
        sorted(x["tag"].split("_c")[1] + x["tag"].split("_")[0] for x in prog)
        == sorted(f"{cc:02d}{a}" for cc in range(8) for a in R.ARMS))
    rr2 = Path(tempfile.mkdtemp())
    dl = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    o2 = subprocess.run([sys.executable, "-m", "harness.run_item9", "--mock", "--allow-unsafe", "--build", str(SCEN / "build"),
                         "--runs-root", str(rr2), "--deadline", dl, "--max-blocks", "2"], cwd=ROOT, capture_output=True, text=True)
    st2 = [json.loads(f.read_text()) for f in rr2.glob("*/item9_status.json")]
    res[f"deadline guard: 10 usable minutes < the prior block estimate ({R.PRIOR_BLOCK_S:.0f} s): cut before block 0"] = (
        o2.returncode == 0 and st2 and st2[0]["status"].startswith("cut before block 0") and st2[0]["cells"] == 0)
    t = R.merge_tallies([{"generated": 3, "excluded_prefix": [1], "reach": {"a": {"attempted": 2, "reached": 1, "reached_no_nudge": 1}}},
                         {"generated": 4, "excluded_prefix": [], "reach": {"a": {"attempted": 1, "reached": 1, "reached_no_nudge": 0}}}])
    res["lane tallies merge"] = t["generated"] == 7 and len(t["excluded_prefix"]) == 1 and t["reach"]["a"] == {
        "attempted": 3, "reached": 2, "reached_no_nudge": 1}
    return res


if __name__ == "__main__":
    r = run()
    for k, v in r.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in r.items() if not v]
    print(f"\n{len(r) - len(bad)}/{len(r)} passed")
    sys.exit(1 if bad else 0)
