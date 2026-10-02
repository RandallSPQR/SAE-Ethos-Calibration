# Benign control actions: external FPR calibration set (for Codex; 2026-10-01)

`benign_control_actions.jsonl` (written by `extract_benign_actions.py`): **4,925 tool calls from 1,398 benign control
rows** of the two dev T3 runs. One line per call made in the continuation (from the decision point); fields: model,
run_id, source_uid, scenario, seed, variant, label, taxonomy, labeler_rules, message_index, step, tool, args,
assistant_text (the turn's own words, tool block removed), result (first 2,000 chars).

| model | run | labels | rows | actions |
|---|---|---|---|---|
| gemma-3-27b-it | run_df0365347b16 (27B T3) | labeler 2026-10-01.2 (`relabel_2026-10-01.2`) | 781 | 2,293 |
| gemma-2-9b-it | 9B T3 joined | as persisted in that run (pre-2026-09-30 rules; not relabeled) | 617 | 2,632 |

Selection: variant = control, current taxonomy = benign, cell not prefix-contaminated. The shared greedy prefix is left
out (it repeats per continuation). Caveats for an FPR use: the 27B's impossible_test control continuations are a
single `submit` each (200 actions); "benign" is the labeler's episode label, so an individual action inside a benign
episode is benign by that episode's outcome, not hand-checked one by one. No held-out path is read.

## Manifest and the no-double-use rule (2026-10-02, Randall)

`benign_control_actions_manifest.json` (written by `make_manifest.py`) lists every source uid, by model. **All 781 27B source
rows are also span-study control rows** (`transgression_spans/control_spans.jsonl`, the role-matched counterparts of the
within-cell analysis). One analysis must not both calibrate on a row and evaluate on it: `make_manifest.check_disjoint(
eval_uids)` raises if it would. Where the span study and an FPR calibration meet, split the 27B rows (e.g. by seed parity)
or calibrate on the 9B rows plus the out-of-family set. **Both sources are Gemma** (27B and 9B); the out-of-family set
(SWE-agent trajectories) is pending with Codex.
