"""Item 6b behavioral manipulation check (gate rules 2026-10-06.1; PREREG_ITEM6B_STEERING.md section 7; amendment 2).

Under steering, the model is asked to state the guaranteed amount and the chance of winning the risky option. N = 108
evaluation prompts: safe 70, n in {10, 20, ..., 180} x 6 cells (disjoint from the CAA training prompts). Greedy, 48 new
tokens. Correct iff BOTH:
  amount       the first integer within 60 characters after a match of guarantee|sure|safe (case-insensitive) == 70
  probability  the text matches PROB_RE (case-insensitive)
Pass at a strength: the one-sided 95 % lower bound of acc(k) - acc(0) >= -0.05, the bound being the 5th percentile of
2,000 paired bootstrap draws over the 108 items (seed 20261006). Instrument floor: acc(0) >= 0.80, else NOT_EVALUABLE.
"""
import re

import numpy as np

from probe.tasks import messages, UNITS

LEVEL = 70
NS = tuple(range(10, 181, 10))
INSTRUCTION_NATIVE = "Answer with exactly one of: Safe Option, Risky Option."
INSTRUCTION = "Before choosing, state the guaranteed amount and the chance of winning the risky option. Then stop."
MAX_NEW = 48
AMOUNT_RE = re.compile(r"(guarantee|sure|safe)", re.I)
PROB_RE = re.compile(r"50 ?%|50 percent|fifty percent|0\.5\b|\bhalf\b|1 in 2|one in two|1/2", re.I)
MARGIN, FLOOR, N_BOOT, SEED = -0.05, 0.80, 2000, 20261006


def items():
    return [{"n": n, "level": LEVEL, "cell": f"{o}/{u}", "cond": {"unit": u, "order": o}}
            for n in NS for o in ("safe_first", "risky_first") for u in UNITS]


def prompt(it):
    m = messages("lottery", it["n"], it["level"], it["cond"])
    assert INSTRUCTION_NATIVE in m[0]["content"]
    return [{"role": "user", "content": m[0]["content"].replace(INSTRUCTION_NATIVE, INSTRUCTION)}]


def parse(text, level=LEVEL):
    """-> (correct, stated_amount or None, probability_stated)."""
    text = text or ""
    amount = None
    for m in AMOUNT_RE.finditer(text):
        k = re.search(r"\d+", text[m.end(): m.end() + 60])
        if k:
            amount = int(k.group(0)); break
    prob = bool(PROB_RE.search(text))
    return (amount == level and prob), amount, prob


def paired_lower_bound(correct_k, correct_0, n_boot=N_BOOT, seed=SEED):
    """5th percentile of the paired bootstrap of mean(correct_k - correct_0) over items (one-sided 95 % lower bound)."""
    a = np.asarray(correct_k, float); b = np.asarray(correct_0, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n_boot, len(a)))
    return float(np.percentile((a[idx] - b[idx]).mean(1), 5))


def verdict(correct_k, correct_0):
    acc0 = float(np.mean(correct_0))
    if acc0 < FLOOR:
        return {"verdict": "NOT_EVALUABLE", "acc0": acc0, "reason": f"unsteered accuracy {acc0:.2f} < floor {FLOOR}"}
    lb = paired_lower_bound(correct_k, correct_0)
    return {"verdict": "PASS" if lb >= MARGIN else "FAIL", "acc0": acc0, "acc": float(np.mean(correct_k)), "lower_bound": lb}
