"""Tests for item 10 (MODEL_PROFILE=gemma-3-27b-it python -m harness.test_item10): the new lists (none reused from items
7-9; neutral free of testing / grading words; pc = item 8's verbatim), the block design, placement at turn_close, and a
two-lane mock block on disjoint sandbox slots, plus the deadline guard (mock backend)."""
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MODEL_PROFILE", "gemma-3-27b-it")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml                                                     # noqa: E402

from harness import run_item10 as R                             # noqa: E402
from harness.fixtures import SCEN                               # noqa: E402
from harness.test_item7b import Capture                         # noqa: E402


def run():
    res = {}
    import modelcfg
    ser = modelcfg.serializer()
    p, psha = R.paraphrases()
    old = set()
    for f in ("item7b_paraphrases.yaml", "item8_paraphrases.yaml", "item9_paraphrases.yaml"):
        for v in yaml.safe_load((ROOT / "config" / f).read_text()).values():
            old |= set(v)
    p8 = yaml.safe_load((ROOT / "config" / "item8_paraphrases.yaml").read_text())
    frozen = json.loads(R.FREEZE.read_text()) if R.FREEZE.exists() else {}
    res["16 new grader + 16 new neutral, none reused from items 7-9, all distinct; pc = item 8's verbatim; sha frozen"] = (
        len(set(p["grader"])) == len(set(p["neutral"])) == 16 and not (set(p["grader"]) | set(p["neutral"])) & old
        and p["pc"] == p8["pc"] and frozen.get("paraphrases_sha256") == psha)
    bad = re.compile(r"test|grad|scor|evaluat|automat|check|verif|correct|pass|bench|harness", re.I)
    res["neutral sentences carry no testing / grading words; every grader sentence carries one"] = (
        not any(bad.search(s) for s in p["neutral"]) and all(bad.search(s) for s in p["grader"]))
    bl = R.blocks()
    main = [c for b in bl for c in b if c[2] != "pc"]
    pairs = {(c[1], c[3]) for c in main if c[2] == "grader"}
    res["design: 7 blocks of 36 cells; 112 distinct (seed, i) pairs; seeds 0-55 x 2; each block holds every i once per arm"] = (
        len(bl) == 7 and all(len(b) == 36 for b in bl) and len(pairs) == 112
        and collections.Counter(s for s, _ in pairs) == {s: 2 for s in range(56)}
        and all(sorted(c[3] for c in b if c[2] == a) == list(range(16)) for b in bl for a in ("grader", "neutral")))
    pcs = [c for b in bl for c in b if c[2] == "pc"]
    res["pc: 4 cells per block, 28 in all, every pc sentence 3-4 times, seeds in 0-55"] = (
        len(pcs) == 28 and all(sum(c[2] == "pc" for c in b) == 4 for b in bl)
        and set(collections.Counter(c[3] for c in pcs).values()) <= {3, 4} and all(0 <= c[1] < 56 for c in pcs))
    from harness.run_harness import run_scenario_seed
    run_cfg = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())
    spec = yaml.safe_load((SCEN / "arm_a" / "impossible_test" / "scenario.yaml").read_text())
    sd = SCEN / "build" / "discover" / "impossible_test" / "seed_000"
    manifest = json.loads((sd / "manifest.json").read_text())
    for arm in ("grader", "neutral", "pc"):
        sentence = p[arm][2]
        agent = Capture(); out = Path(tempfile.mkdtemp())
        run_scenario_seed(agent, spec, sd, manifest, run_cfg["sampling"], 1, str(out), str(out / "l.jsonl"), "t10",
                          {"generated": 0, "excluded_prefix": [], "reach": {}}, hcfg={"concurrency": 1}, variants={"full"},
                          insert={"text": sentence, "position": R.POSITION}, row_extra={"item10": {"arm": arm}})
        first = ser.serialize_messages(agent.first).split("<start_of_turn>user\n", 1)[1].split("<end_of_turn>", 1)[0]
        res[f"{arm}: the sentence is the closing line of the serialized first user turn"] = first.rstrip().endswith(sentence)
    # one mock block in-process, recording the slot of every Sandbox the harness builds
    import harness.run_harness as RH
    real, slots = RH.Sandbox, set()

    class Rec(real):
        def __init__(self, *args, slot=0, **kw):
            slots.add(slot); super().__init__(*args, slot=slot, **kw)
    rr = Path(tempfile.mkdtemp()); RH.Sandbox, argv = Rec, sys.argv
    sys.argv = ["run_item10", "--mock", "--allow-unsafe", "--build", str(SCEN / "build"), "--runs-root", str(rr), "--max-blocks", "1"]
    try:
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            R.main()
    finally:
        RH.Sandbox, sys.argv = real, argv
    st = [json.loads(f.read_text()) for f in rr.glob("*/item10_status.json")]
    prog = [json.loads(l) for f in rr.glob("*/item10_progress.jsonl") for l in open(f)]
    res["mock block: 36 cells, 18 per lane, prefixes on slots 0 and 16 only, status complete"] = (
        st and st[0]["cells"] == 36 and st[0]["status"] == "complete"
        and collections.Counter(x["lane"] for x in prog) == {0: 18, 1: 18} and slots == {0, 16})
    rr2 = Path(tempfile.mkdtemp())
    dl = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    o = subprocess.run([sys.executable, "-m", "harness.run_item10", "--mock", "--allow-unsafe", "--build", str(SCEN / "build"),
                        "--runs-root", str(rr2), "--deadline", dl, "--max-blocks", "2"], cwd=ROOT, capture_output=True, text=True)
    st2 = [json.loads(f.read_text()) for f in rr2.glob("*/item10_status.json")]
    res[f"deadline guard: 10 usable minutes < the prior block ({R.PRIOR_BLOCK_S:.0f} s): cut before block 0"] = (
        o.returncode == 0 and st2 and st2[0]["status"].startswith("cut before block 0"))
    return res


if __name__ == "__main__":
    r = run()
    for k, v in r.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in r.items() if not v]
    print(f"\n{len(r) - len(bad)}/{len(r)} passed")
    sys.exit(1 if bad else 0)
