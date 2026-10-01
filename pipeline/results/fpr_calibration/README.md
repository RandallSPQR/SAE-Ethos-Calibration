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
