# Probe-regime transfer, run 1 (2026-10-02): INVALID, agent-regime trials truncated

Queue item 5; gate rules 2026-10-02.1. Pod `ep8negjaejlnmc`, 2 x A100 SXM 80 GB, EUR-IS-1, code 61cca00.
18:21:42 to 19:05:50 UTC (44 min), about $2.34 at $3.18/h; terminated by API after copy-back.

**Do not read the verdicts in `probe_agent/TRANSFER.md` as the pre-registered test.** The agent-regime sampler was
capped at max_tokens = 96 (`probe/synth_trials.py`, my error). In the agent frame the model reasons in prose before its
submit call, so most replies were cut before the call closed:

| task | agent trials | dropped | dropped at the 96-token cap | kept |
|---|---|---|---|---|
| lottery | 1,120 | 670 (60 %) | 669 | 450 |
| ultimatum | 248 | 225 (91 %) | 225 | 23 (all Accept) |

The cut is not random with respect to the choice (descriptive check, not used for any verdict):
- lottery drop rate by n / safe: < 0.8: 45 %; 0.8 to 1.5: 46 %; 1.5 to 2.5: 64 %; >= 2.5: 83 %.
- on the same items in the native regime, P(risky) = 0.35 for the kept agent items vs 0.53 for the dropped ones.
- among cut lottery replies whose partial submit answer is visible, 57 begin "Risky" and 52 "Safe", concentrated at
  high and low n / safe respectively.

So the lottery table (cleaned PASS at layers 30 / 38 / 40 / 46, agent AUROC 0.76 to 0.88) is an AUROC on a
length-selected subset with class balance 0.16, not on the agent regime; the ultimatum is NOT_EVALUABLE (one class).
The native regime is unaffected (16-token cap, 0 drops; lottery sp@50 = 89.3, ultimatum sp = 5.5).

Fix (committed with this record): agent cap = 1024 (the harness per-turn budget, g2_max_tokens); every trial records
`truncated`; the agent regime STOPs (non-zero exit, driver exit 9) when more than 1 % of a task's trials hit the cap.
The criterion (gate rules 2026-10-02.1) is unchanged. A re-run needs a pod (Randall's approval).

`activations.npz` files (316 MB) are gitignored and kept locally.
