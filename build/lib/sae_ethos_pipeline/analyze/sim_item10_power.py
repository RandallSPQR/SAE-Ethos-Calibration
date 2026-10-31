"""Item 10 power simulation for the registered layout (analyze/PREREG_ITEM10_GRADER.md section 5): 100 runs per cell at
neutral 0.68, grader 0.68 + d, pc 0.35; honest reports neutral 0.11, grader 0.11 - 0.09 d / 0.08, pc 0.48; ICC ~0.02,
paraphrase sd 0.15; 11 blocks and 10 (one block cut).
  python analyze/sim_item10_power.py <out.json>
"""
import sys, io, contextlib, json; sys.path.insert(0, '.')
import numpy as np
from analyze import test_item10 as T, item10_grader as A
out = {}
for d in (0.08, 0.06, 0.05):
    for nb in (11, 10):
        trans = {"neutral": 0.68, "grader": 0.68 + d, "pc": 0.35}
        honest = {"neutral": 0.11, "grader": round(0.11 - 0.09 * d / 0.08, 4), "pc": 0.48}
        pc = pr = se = 0; N = 100; iccs = []
        for s in range(N):
            with contextlib.redirect_stdout(io.StringIO()):
                r = A.analyze(T.fake_run(trans=trans, honest=honest, seed=20000 + 1000 * nb + int(d * 1000) + s, n_blocks=nb))
            v = r["verdicts"]; pc += v["position_check"] == "PASS"; pr += v["primary"] == "PASS"; se += v["secondary"] == "PASS"
            iccs.append(r["effects"]["primary"]["icc_within_prefix"])
        out[f"+{d:.2f}, {nb} blocks"] = {"per_arm": nb * 64, "grader_honest": honest["grader"], "position_check": pc / N,
                                         "primary": pr / N, "secondary": se / N, "icc": round(float(np.mean(iccs)), 3)}
        print(f"+{d:.2f} {nb} blocks ({nb*64}/arm): pc {pc/N:.2f} primary {pr/N:.2f} secondary {se/N:.2f} icc {np.mean(iccs):.3f}", flush=True)
json.dump(out, open(sys.argv[1], "w"), indent=1)
