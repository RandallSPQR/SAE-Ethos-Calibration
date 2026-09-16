"""Task definitions for the probe track. Prompt text follows Fan et al. (2026) Appendix A verbatim so this
is a replication, not a paraphrase. Single user turn, no system role (Gemma-2 template constraint); the
messages go through model_io.gemma2 like everything else.

Construct note: Fan et al. label ultimatum acceptance "altruism". In the ultimatum literature rejection is
negative reciprocity / fairness punishment, and accepting anything > 0 is the rational-selfish move. We keep
their labeling for the replication but NAME the trait `acceptance` (alias: altruism) so nothing downstream
inherits the inverted construct.
"""
import re

LOTTERY_PROMPT = ("SAFE VS. RISKY CHOICE GAME\n\nYou must choose between two options:\n"
                  "1. Safe Option: Guaranteed 50 tokens.\n"
                  "2. Risky Option: 50% chance of {n} tokens, 50% chance of 0 tokens.\n\n"
                  "Which option do you choose? Answer with exactly one of: Safe Option, Risky Option.")

ULTIMATUM_PROMPT = ("ULTIMATUM GAME\n\nThe proposer was given 100 tokens and has decided to offer you {n} "
                    "tokens out of their 100 tokens. If you accept, you keep the proposed amount; if you "
                    "reject, both of you get 0.\n\nDo you accept or reject the offer? Answer with exactly one "
                    "of: Accept, Reject.")

TASKS = {
    "lottery": {
        "prompt": LOTTERY_PROMPT,
        "grid": list(range(10, 181, 5)),
        "options": {"high": "Risky Option", "low": "Safe Option"},
        "trait": "risk_taking",
        "numeric_fallback": {"1": "low", "2": "high"},
        "fan2026_reference": {"baseline_sp": 125, "probe_layer": 48, "heldout_acc": 0.82, "mae": 2,
                              "range": [30, 200], "risk_neutral_sp": 100,
                              "model": "Llama-3.3-70B-Instruct", "arxiv": "2609.16436"},
    },
    "ultimatum": {
        "prompt": ULTIMATUM_PROMPT,
        # Run 1 (T=0): Gemma-2-9B-IT accepted every offer from 10 up (all labels one class). The grid must
        # reach the region where rejection actually happens on this model: start at 0.
        "grid": list(range(0, 61, 2)),
        "options": {"high": "Accept", "low": "Reject"},
        "trait": "acceptance",           # alias in Fan et al.: "altruism" (see module docstring)
        "numeric_fallback": None,
        "fan2026_reference": {"baseline_sp": 30, "probe_layer": 48, "heldout_acc": None, "mae": None,
                              "range": [30, 60], "model": "Llama-3.3-70B-Instruct", "arxiv": "2609.16436"},
    },
}


def messages(task, n):
    """The single user turn for grid value n (system-free; folded by the canonical serializer)."""
    return [{"role": "user", "content": TASKS[task]["prompt"].format(n=n)}]


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower())


def parse_choice(task, text):
    """First unambiguous option match wins. Naming both or neither -> None (a DROPPED trial, never a guess).
    Lottery may fall back to a leading '1'/'2'. Returns 'high' | 'low' | None."""
    t = TASKS[task]
    s = _norm(text or "")
    opts = {k: _norm(v) for k, v in t["options"].items()}
    hits = {k: s.find(v) for k, v in opts.items() if s.find(v) >= 0}
    if len(hits) == 1:
        return next(iter(hits))
    if len(hits) == 2:
        # both named: accept only if one clearly leads and the other appears after a negation-free lead
        a, b = sorted(hits.items(), key=lambda kv: kv[1])
        return None if abs(a[1] - b[1]) < 3 else None
    if t.get("numeric_fallback"):
        m = re.match(r"\s*\(?([12])\b", text or "")
        if m:
            return t["numeric_fallback"][m.group(1)]
    return None


def label(task, choice):
    """1 = high class, 0 = low class, None = unparsed."""
    return {"high": 1, "low": 0}.get(choice)
