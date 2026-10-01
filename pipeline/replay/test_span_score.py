"""Unit tests for replay.span_score (MODEL_PROFILE=gemma-3-27b-it python -m replay.test_span_score). No model, no GPU:
the guards, the job list, the input construction (prefix from the canonical serializer + the turn's own sampled ids cut at
the first stop), and the language-layer assertion on a fake multimodal module tree."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MODEL_PROFILE", "gemma-3-27b-it")

from replay import span_score as S


class Tok:
    """Character-level fake: one id per character, BOS = 2 (add_special_tokens)."""
    def __call__(self, text, add_special_tokens=True, **kw):
        return {"input_ids": ([2] if add_special_tokens else []) + [1000 + ord(c) for c in text]}


class LM:
    tokenizer = Tok()


def _row():
    return {"uid": "impossible_test/seed_001/full/c00", "messages": [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "task"},
        {"role": "assistant", "content": "a", "tokens": {"sampled_ids": [5, 6, 106, 1], "sampled_logprobs": [-.1, -.2, -.3, -.4],
                                                         "sampled_top2_margin": [1, 2, 3, 4]}},
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "content": "b", "tokens": {"sampled_ids": [7, 8, 9], "sampled_logprobs": [-.5, -.6, -.7]}},
        {"role": "assistant", "content": "c"}]}


def run():
    out = {}
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        for bad in ("held_out/x.jsonl", "data/heldout_pairs", "matched_pairs/run"):
            try:
                S.refuse_heldout(t / bad); out[f"refuses {bad}"] = False
            except SystemExit:
                out[f"refuses {bad}"] = True
        try:
            S.refuse_heldout(t / "results/t4_27b/transgression_spans.jsonl"); out["accepts a dev path"] = True
        except SystemExit:
            out["accepts a dev path"] = False
        (t / "fail.md").write_text("primary 30/35\n"); (t / "pass.md").write_text("**PASS.** Primary span 35/35\n")
        for f, want in (("missing.md", False), ("fail.md", False), ("pass.md", True)):
            try:
                S.require_handcheck(t / f); got = True
            except SystemExit:
                got = False
            out[f"hand-check gate {f}"] = got == want
        sp = t / "spans.jsonl"
        sp.write_text("\n".join(json.dumps(x) for x in [
            {"uid": "u1", "locator_version": "x", "status": "ok", "act": {"message_index": 10}, "report": {"message_index": 20},
             "attempt": {"message_index": 10}},
            {"uid": "u2", "locator_version": "x", "status": "ok", "cell_excluded": True, "act": {"message_index": 4}, "report": None},
            {"uid": "u3", "locator_version": "x", "status": "act_in_prefix", "act": None, "report": None}]) + "\n")
        cp = t / "ctl.jsonl"
        cp.write_text(json.dumps({"uid": "c1", "roles": {"submit": {"message_index": 12}, "commit": None,
                                                         "legacy_removal": {"message_index": 10}}}) + "\n")
        j = S.span_jobs(sp, cp)
        out["jobs: act+attempt merged, report kept, excluded/failed rows dropped, control roles"] = \
            j == {"u1": [("act+attempt", 10), ("report", 20)], "c1": [("control_legacy_removal", 10), ("control_submit", 12)]}
        (t / "nolocator.jsonl").write_text(json.dumps({"uid": "u", "status": "ok"}) + "\n")
        try:
            S.span_jobs(t / "nolocator.jsonl"); out["refuses a non-locator spans file"] = False
        except SystemExit:
            out["refuses a non-locator spans file"] = True
    import modelcfg
    ids, (s, e), core, lp, mg = S.span_inputs(LM(), _row(), 2)
    prefix = modelcfg.serializer().apply_to_tokenizer(Tok(), _row()["messages"][:2], add_generation_prompt=True)
    out["inputs: serializer prefix + sampled core cut at the first stop"] = ids == list(prefix) + [5, 6] and (s, e) == (len(prefix), len(prefix) + 2) \
        and core == [5, 6] and lp == [-.1, -.2] and mg == [1, 2]
    ids2, (s2, e2), core2, _, _ = S.span_inputs(LM(), _row(), 4)
    out["inputs: a later turn sees the earlier turns as text"] = core2 == [7, 8, 9] and ids2[:s2] == list(
        modelcfg.serializer().apply_to_tokenizer(Tok(), _row()["messages"][:4], add_generation_prompt=True))
    for k, why in ((5, "no sampled_ids"), (3, "not an assistant turn")):
        try:
            S.span_inputs(LM(), _row(), k); out[f"refuses a span with {why}"] = False
        except ValueError:
            out[f"refuses a span with {why}"] = True
    # language-layer assertion on a fake Gemma3ForConditionalGeneration-shaped tree (vision tower has layers too)
    import torch.nn as nn

    class Fake(nn.Module):
        def __init__(self):
            super().__init__()
            self.model = nn.Module()
            self.model.language_model = nn.Module(); self.model.language_model.layers = nn.ModuleList([nn.Linear(2, 2) for _ in range(4)])
            self.model.vision_tower = nn.Module(); self.model.vision_tower.layers = nn.ModuleList([nn.Linear(2, 2) for _ in range(4)])
            self.config = type("C", (), {"num_hidden_layers": 4})()

    class FLM:
        def __init__(self, layers_from):
            self.model = Fake()
            self._from = layers_from
    f_ok = FLM("language")
    import replay.modelload as ML
    orig = ML.decoder_layers
    try:
        ML.decoder_layers = lambda lm: lm.model.model.language_model.layers
        out["language layers accepted"] = S.assert_language_layers(f_ok, [1, 3]) == {1: "model.language_model.layers.1",
                                                                                     3: "model.language_model.layers.3"}
        ML.decoder_layers = lambda lm: lm.model.model.vision_tower.layers
        try:
            S.assert_language_layers(f_ok, [1]); out["vision-tower layers refused"] = False
        except AssertionError:
            out["vision-tower layers refused"] = True
    finally:
        ML.decoder_layers = orig
    return out


if __name__ == "__main__":
    res = run()
    for k, v in res.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in res.items() if not v]
    print(f"\n{len(res) - len(bad)}/{len(res)} passed")
    sys.exit(1 if bad else 0)
