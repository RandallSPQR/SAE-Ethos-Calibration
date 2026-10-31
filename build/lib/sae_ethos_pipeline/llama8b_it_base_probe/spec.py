"""What a project defines: its readouts per model family and what to capture.

A Readout is one question asked of one item, read at the answer position over a fixed set of levels. Two kinds:
  cue readout     `options`: one string per level; the prompt (the project's render) ends with a cue and each option
                  must add exactly ONE token after it (first_token=True: the option's first word). Typical on the base
                  model ("...\\nAnswer: " + "1".."7").
  answer readout  `levels`: the required spellings per level, read after the generation prompt (nothing prefilled
                  unless `prefill`); exact_end=True scores multi-token spellings exactly with the end-of-label factor
                  (score.level_probs with continuation ids). Typical on the Instruct model.
level_values default to 1..L; `expected` in the capture record is the mass-renormalized mean of these values.

A ProjectSpec names the readouts per family ('llama3_base', 'llama3'), the primary readout per family (its prompt is the
one whose hidden states are captured at every named position), the layers, the readouts whose answer-position state is
also captured (also_last), and a spans(item) function giving char spans in item["text"] (tokens.token_positions):
  {name: (start, end)}            -> activations name_last (last token of the span) and name_mean (mean over its tokens)
  {name: [(start, end), ...]}     -> activations name: the first token of each span, stacked [n_spans, d]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from llama8b_it_base_probe import ops, tokens


@dataclass(frozen=True)
class Readout:
    name: str
    render: Callable[[dict], list]                  # item -> messages
    options: tuple = ()                             # cue readout: one option per level
    first_token: bool = False
    levels: tuple = ()                              # answer readout: (spellings,) per level
    exact_end: bool = False
    level_values: tuple | None = None
    prefill: str = ""

    def __post_init__(self):
        if bool(self.options) == bool(self.levels):
            raise ValueError(f"readout {self.name!r}: give exactly one of options (cue) or levels (answer)")
        if self.level_values is not None and len(self.level_values) != self.n_levels:
            raise ValueError(f"readout {self.name!r}: {len(self.level_values)} level_values for {self.n_levels} levels")

    @property
    def n_levels(self) -> int:
        return len(self.options or self.levels)

    def values(self) -> tuple:
        return tuple(self.level_values) if self.level_values is not None else tuple(range(1, self.n_levels + 1))

    def messages(self, item) -> list:
        return self.render(item)

    def forms(self, tok, ser, messages) -> list:
        """[[ids of each form] per level], tokenized after this prompt."""
        if self.options:
            return tokens.single_token_forms(tok, ser, messages, self.options, self.first_token, self.prefill)
        ans = ops.answer_tokens(tok, ser, messages, self.prefill, {li: list(sp) for li, sp in enumerate(self.levels)})
        return [[f["ids"] for f in ans[li]] for li in sorted(ans)]


@dataclass(frozen=True)
class ProjectSpec:
    readouts: Mapping[str, Sequence[Readout]]       # family -> readouts, in record order
    primary: Mapping[str, str]                      # family -> readout whose prompt feeds the activations
    layers: tuple = ()
    also_last: tuple = ()                           # readouts whose last-position state is kept as last_<name>
    spans: Callable[[dict], Mapping] | None = None
    record_fields: tuple = ()                       # item fields copied into each readouts.jsonl record
    meta: Mapping = field(default_factory=dict)     # free-form, written into capture_meta.json

    def readouts_for(self, family) -> tuple:
        if family not in self.readouts:
            raise KeyError(f"spec has no readouts for family {family!r} (has {sorted(self.readouts)})")
        return tuple(self.readouts[family])

    def primary_for(self, family) -> str:
        p = self.primary[family]
        if p not in {r.name for r in self.readouts_for(family)}:
            raise KeyError(f"primary readout {p!r} is not among {family!r}'s readouts")
        return p
