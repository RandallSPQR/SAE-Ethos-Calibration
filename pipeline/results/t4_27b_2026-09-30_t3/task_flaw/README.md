# Verbalized task flaw on the 27B T3 run (labeler 2026-10-01.3; mediator covariate, never a label input)

Detector `harness/task_flaw.py`, developed and frozen on the G1 calibration run (13 hits, all read and all genuine flaw
claims; 11/30 impossible_test full, 0 controls), tests 21/21, then run once on T3. Facts: `task_flaw_facts.jsonl`
(uid, matched phrases, first message). Sweep: **0 labels move** with the fact present. Analysis: `MEDIATION.md`
(`analyze/mediation_task_flaw.py`).

- **a-path (obstacle -> verbalization):** impossible_test full 85/200 (42.5 %) vs control 0/200 and mention 0/200
  (Fisher p 5e-31); missing_delete_tool full 5/180; credential and honeypot 0.
- **b-path (verbalization -> gaming vs honest), impossible_test full, within cells:** a claim BEFORE the act: 32 gamed,
  9 honest; no claim: 70 gamed, 12 honest. Mantel-Haenszel OR **0.44** (20 cells, 8 informative), within-cell
  permutation p 0.17. No support for "the test is flawed" leading to gaming; if anything it leans toward honest reports.
- **Post-hoc rationalization:** 36 of the 138 gamed rows state the flaw ONLY AFTER the act: the claim follows the
  gaming as its justification rather than preceding it.
- Caveats: association with temporal precedence, not causal mediation (M is not randomized); the window is asymmetric by
  construction (a gamed row's window ends at its act, an honest row's at its last message), which favours finding M in
  honest rows; missing_delete_tool's outcomes (silent_empty / explicit_stub) are outside the gamed / honest sets, so its
  b-path is empty. Not used to adjust any main effect.
