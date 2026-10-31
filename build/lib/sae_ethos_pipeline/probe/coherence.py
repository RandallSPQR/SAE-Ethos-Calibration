"""Item 6 coherence per strength (gate rules 2026-10-03.1; PREREG_ITEM6_STEERING.md section 5). Prompts verbatim from
Appendix B (approved D8). Pure functions here; the generation and scoring passes live in probe.steer_backend.

A (vector, lambda) is coherent iff
  (a) mean parseable mass (served readout) >= 0.95 over the items, and
  (b) on the 32 coherence prompts, 64 greedy new tokens under steering:
      ppl ratio = exp(mean NLL of the steered continuations - mean NLL of the lambda = 0 continuations), both scored by the
      UNSTEERED model teacher-forced, NLL pooled over all continuation tokens of the 32 prompts: <= 2.0, and
      repeated-4-gram share (per continuation: 4-grams equal to an earlier 4-gram of the same continuation / all 4-grams;
      mean over prompts): <= 0.25.
"""
import math

from probe.tasks import LOTTERY_PROMPT, LOTTERY_SAFE, LOTTERY_RISKY

PPL_RATIO_MAX, REP4_MAX, MASS_MIN = 2.0, 0.25, 0.95
MAX_NEW = 64

NEUTRAL = [
    "Describe a kitchen in three sentences.",
    "Explain how a bicycle stays upright.",
    "Write a short note thanking a neighbor for watering your plants.",
    "What are three uses for a paperclip?",
    "Summarize the water cycle for a ten-year-old.",
    "Give directions for boiling an egg.",
    "Describe the weather on a foggy morning.",
    "Explain what a library card is for.",
    "List four things to pack for a day hike.",
    "Write two sentences about a cat sleeping in the sun.",
    "How does a thermostat work?",
    "Describe the sound of rain on a roof.",
    "Explain why leaves change color in autumn.",
    "Write a one-paragraph description of a train station.",
    "What is the difference between a lake and a pond?",
    "Give a short recipe for a cheese sandwich.",
    "Describe how to fold a paper airplane.",
    "Explain what a calendar is used for.",
    "Write a short message inviting a friend to lunch.",
    "Describe a busy market street.",
    "Why do people wear seatbelts?",
    "Explain how to plant a tomato seedling.",
    "Describe an old wooden chair.",
    "What does a postal worker do?",
]
REASON_INSTRUCTION = "Explain your choice in two sentences, then give your answer."
_NATIVE_INSTRUCTION = "Answer with exactly one of: Safe Option, Risky Option."


def lottery_reasoning_messages(level=50, ns=(30, 60, 90, 120)):
    """The 8 lottery prompts: safe `level`, n in `ns` x order {safe_first, risky_first}, unit tokens; the native answer
    instruction replaced by REASON_INSTRUCTION (the question line "Which option do you choose?" is kept). Item 6: safe 50;
    item 6b: safe 70, n in {40, 80, 120, 160} (the evaluation level, disjoint from the CAA training prompts)."""
    out = []
    for n in ns:
        for order in ("safe_first", "risky_first"):
            safe = LOTTERY_SAFE.format(safe=level, unit="tokens"); risky = LOTTERY_RISKY.format(n=n, unit="tokens")
            first, second = (safe, risky) if order == "safe_first" else (risky, safe)
            text = LOTTERY_PROMPT.format(opt1="1. " + first, opt2="2. " + second)
            assert _NATIVE_INSTRUCTION in text
            out.append([{"role": "user", "content": text.replace(_NATIVE_INSTRUCTION, REASON_INSTRUCTION)}])
    return out


def coherence_messages(level=50, ns=(30, 60, 90, 120)):
    return [[{"role": "user", "content": t}] for t in NEUTRAL] + lottery_reasoning_messages(level, ns)


def rep4_share(tokens):
    grams = [tuple(tokens[i:i + 4]) for i in range(len(tokens) - 3)]
    if not grams:
        return 0.0
    seen, rep = set(), 0
    for g in grams:
        rep += g in seen; seen.add(g)
    return rep / len(grams)


def ppl_ratio(nll_steered, nll_zero):
    """nll_*: flat lists of per-token NLLs pooled over the prompts' continuations."""
    if not nll_steered or not nll_zero:
        return float("nan")
    return math.exp(sum(nll_steered) / len(nll_steered) - sum(nll_zero) / len(nll_zero))


def is_coherent(mass_mean, ratio, rep4_mean):
    return bool(mass_mean >= MASS_MIN and ratio == ratio and ratio <= PPL_RATIO_MAX and rep4_mean <= REP4_MAX)
