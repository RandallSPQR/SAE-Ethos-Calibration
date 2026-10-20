"""Example project: how confident is a person that an outdoor plan stays dry, given weather evidence.

This is a toy to show what a project supplies; nothing here is validated. A real project would (1) pass
tokens.check_readout for every readout on both tokenizers over all its items (test_example_cpu.py does that here),
(2) establish behavioural validity of the readout for its construct, and (3) pre-register its gates and statistics.

Readouts
  base (llama3_base, plain-text stem ending in a cue; single token at the boundary)
    expect   "On a 1-5 scale, how confident is {person} that {event}?\\nAnswer: " + "1".."5"      PRIMARY
    verbal   "How likely is it that {event}? Answer with one of: <labels>.\\nAnswer:" + " <label>" (first token)
  Instruct (llama3, answer after the generation prompt, nothing prefilled)
    expect   the same question + " Answer with the number only."; digits, single token
    verbal   the labels, lower- and upper-case first letter, scored exactly with the end-of-label factor  PRIMARY
Activations (layers 15, 19, 28): from the primary prompt, the evidence sentence (evidence_last, evidence_mean) and the
first token of each cue phrase (cue_words, [n_cues, d]); plus the answer position of expect and verbal on both models.

  MODEL_PROFILE=llama-3.1-8b-it llama-probe-capture --spec dry_weather.spec:SPEC --items example/dry_weather/items.jsonl --out OUT
"""
from llama8b_it_base_probe.spec import ProjectSpec, Readout

LABELS = ["impossible", "unlikely", "possible", "likely", "certain"]
EXPECT_Q = "On a 1-5 scale, how confident is {person} that {event}?"
VERBAL_Q = "How likely is it that {event}? Answer with one of: " + ", ".join(LABELS) + "."


def _base(question, cue):
    return lambda it: [{"role": "user", "content": f"{it['text']}\n{question.format(**it)}\n{cue}"}]


def _chat(question):
    return lambda it: [{"role": "user", "content": f"{it['text']}\n{question.format(**it)}"}]


def spans(it):
    t, ev = it["text"], it["evidence"]
    a = t.index(ev)
    return {"evidence": (a, a + len(ev)),
            "cue_words": [(t.index(c, a), t.index(c, a) + len(c)) for c in it["cue_words"]]}


BASE = (
    Readout("expect", _base(EXPECT_Q, "Answer: "), options=tuple(str(i) for i in range(1, 6))),
    Readout("verbal", _base(VERBAL_Q, "Answer:"), options=tuple(f" {l}" for l in LABELS), first_token=True),
)
CHAT = (
    Readout("expect", _chat(EXPECT_Q + " Answer with the number only."), levels=tuple((str(i),) for i in range(1, 6))),
    Readout("verbal", _chat(VERBAL_Q + " Answer with the label only."),
            levels=tuple((l, l.capitalize()) for l in LABELS), exact_end=True),
)

SPEC = ProjectSpec(readouts={"llama3_base": BASE, "llama3": CHAT}, primary={"llama3_base": "expect", "llama3": "verbal"},
                   layers=(15, 19, 28), also_last=("expect", "verbal"), spans=spans, record_fields=("set",),
                   meta={"project": "dry_weather example", "levels": "1..5"})
