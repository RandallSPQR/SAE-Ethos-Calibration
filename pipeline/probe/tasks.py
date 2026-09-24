"""Task definitions for the probe track. Prompt text follows Fan et al. (2026) Appendix A; the numbers, the
unit word and the option order vary per trial so the probe has to find the DECISION VARIABLE (the gamble's
attractiveness relative to the sure thing) rather than the literal number in the prompt. Single user turn,
no system role (Gemma-2 template constraint); messages go through model_io.gemma2 like everything else.

Rules 2026-09-16.3 (see gates/CHANGELOG.md): the lottery is two-dimensional (safe amount x risky reward);
one whole safe level is held out for the honest held-out accuracy; surface variation (order, unit) is
deliberate rather than accidental.

Construct note: Fan et al. label ultimatum acceptance "altruism". In the ultimatum literature rejection is
negative reciprocity / fairness punishment, and accepting anything > 0 is the rational-selfish move. We keep
their labeling for the replication but NAME the trait `acceptance` (alias: altruism).
"""
import re

UNITS = ["tokens", "points", "dollars"]

LOTTERY_PROMPT = ("SAFE VS. RISKY CHOICE GAME\n\nYou must choose between two options:\n"
                  "{opt1}\n{opt2}\n\n"
                  "Which option do you choose? Answer with exactly one of: Safe Option, Risky Option.")
LOTTERY_SAFE = "Safe Option: Guaranteed {safe} {unit}."
LOTTERY_RISKY = "Risky Option: 50% chance of {n} {unit}, 50% chance of 0 {unit}."

ULTIMATUM_PROMPT = ("ULTIMATUM GAME\n\nThe proposer was given 100 {unit} and has decided to offer you {n} "
                    "{unit} out of their 100 {unit}. If you accept, you keep the proposed amount; if you "
                    "reject, both of you get 0.\n\nDo you accept or reject the offer? Answer with exactly one "
                    "of: Accept, Reject.")

TASKS = {
    "lottery": {
        "grid": list(range(10, 181, 5)),          # risky reward n
        "levels": [30, 50, 70, 100],              # safe amount; 50 is Fan et al.'s condition (the reference)
        "reference_level": 50,
        "heldout_level": 70,                      # an ENTIRE safe level held out of probe training
        "options": {"high": "Risky Option", "low": "Safe Option"},
        "trait": "risk_taking",
        "decision_variable": "n / safe (attractiveness of the gamble relative to the sure thing)",
        # calibration targets as RATIOS of the safe amount (the construct), realized at the reference level:
        # 0.6..2.0 x 50 = 30..100. Fan et al.'s absolute list (40..175) is Llama's scale, where baseline sat at 125.
        "target_ratios": [0.6, 0.8, 1.0, 1.2, 1.5, 2.0],
        "fan2026_reference": {"baseline_sp": 125, "probe_layer": 48, "heldout_acc": 0.82, "mae": 2,
                              "range": [30, 200], "risk_neutral_sp": 100,
                              "model": "Llama-3.3-70B-Instruct", "arxiv": "2609.16436"},
    },
    "ultimatum": {
        "grid": list(range(0, 61, 2)),             # offer n; from 0 because Gemma-2-9B-IT accepts everything >= 10 at T=0
        "levels": [None], "reference_level": None, "heldout_level": None,
        "options": {"high": "Accept", "low": "Reject"},
        "trait": "acceptance",                      # alias in Fan et al.: "altruism"
        "decision_variable": "offer n (out of 100)",
        "fan2026_reference": {"baseline_sp": 30, "probe_layer": 48, "heldout_acc": None, "mae": None,
                              "range": [30, 60], "model": "Llama-3.3-70B-Instruct", "arxiv": "2609.16436"},
    },
}


def conditions(task, n, seed):
    """Surface conditions for trial (n, seed): unit word and option order cycle with the seed so every grid
    point sees every surface across its agents. Returns a dict recorded on the trial."""
    unit = UNITS[seed % len(UNITS)]
    order = "safe_first" if (seed // len(UNITS)) % 2 == 0 else "risky_first"
    return {"unit": unit, "order": order}


def messages(task, n, level=None, cond=None):
    """The single user turn. level = safe amount (lottery) or None; cond from conditions()."""
    cond = cond or {"unit": "tokens", "order": "safe_first"}
    unit = cond["unit"]
    if task == "lottery":
        safe = LOTTERY_SAFE.format(safe=level if level is not None else TASKS["lottery"]["reference_level"], unit=unit)
        risky = LOTTERY_RISKY.format(n=n, unit=unit)
        first, second = (safe, risky) if cond["order"] == "safe_first" else (risky, safe)
        text = LOTTERY_PROMPT.format(opt1="1. " + first, opt2="2. " + second)
    else:
        text = ULTIMATUM_PROMPT.format(n=n, unit=unit)
    return [{"role": "user", "content": text}]


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower())


def parse_choice(task, text, cond=None):
    """First unambiguous option match wins. Naming both or neither -> None (a DROPPED trial, never a guess).
    Lottery may fall back to a leading '1'/'2', mapped through the trial's option order."""
    t = TASKS[task]
    s = _norm(text or "")
    opts = {k: _norm(v) for k, v in t["options"].items()}
    hits = {k: s.find(v) for k, v in opts.items() if s.find(v) >= 0}
    if len(hits) == 1:
        return next(iter(hits))
    if len(hits) == 2:
        return None
    if task == "lottery":
        m = re.match(r"\s*\(?([12])\b", text or "")
        if m:
            order = (cond or {}).get("order", "safe_first")
            first_is_safe = order == "safe_first"
            return ("low" if first_is_safe else "high") if m.group(1) == "1" else ("high" if first_is_safe else "low")
    return None


def label(task, choice):
    """1 = high class, 0 = low class, None = unparsed."""
    return {"high": 1, "low": 0}.get(choice)
