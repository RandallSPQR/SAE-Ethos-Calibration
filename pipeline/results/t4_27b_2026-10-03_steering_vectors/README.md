# Item 6 steering vectors, FROZEN (gate rules 2026-10-03.1)

Built 2026-10-03 by `python -m probe.vectors --run-dir results/t4_27b_2026-10-02_probe_transfer_run2 --out results/t4_27b_2026-10-03_steering_vectors` after the
registration commit (e400a62). npz sha256 `302557c37fec4dcef301e3594e3378df1ac23631059326e6821564a51a86c22d` (also in `probe/STEERING_FREEZE.json`); an independent
rebuild produced identical bytes. 32 vectors: probe_clean at 4 sites; placebos 16 at lottery L38, 4 elsewhere.

| site | role | transfer (2026-10-02.1, re-scored) | cos(probe_clean, n-dir) homogeneous below / above | AUROC pooled: probe / n / n÷safe | AUROC held-out level 70: probe / n |
|---|---|---|---|---|---|
| lottery_L38 | primary | 0.839 PASS | 0.019 (43 pts) / 0.095 (28 pts) | 0.996 / 0.867 / 0.916 | 0.992 / 0.889 |
| lottery_L30 | secondary | 0.865 PASS | 0.020 (43 pts) / 0.090 (28 pts) | 0.967 / 0.867 / 0.916 | 0.926 / 0.889 |
| ultimatum_L40 | exploratory | 0.866 PASS | - (2 pts) / -0.002 (27 pts) | 1.000 / 0.995 / - | - / - |
| ultimatum_L46 | exploratory | 0.841 PASS | - (2 pts) / 0.003 (27 pts) | 1.000 / 0.995 / - | - / - |

Descriptives gate nothing (ruling 2). Reading:
- **The n direction from choice-homogeneous prompts.** probe_clean's cosine with it is 0.02 below the switching
  point, about the size of a random direction (99th percentile ~0.035 at d = 5376). Above it, the cosine is
  0.09-0.10, small but above that null. Where the choice does not vary, the direction carries little of the number.
  The ultimatum has too few all-reject offers (2) for the "below" region.
- **AUROC pooled across safe levels.** Raw n 0.867 and n/safe 0.916 are both below the probe's 0.996 (L38). The pooled
  set includes training trials, so the out-of-sample comparison is the held-out level 70, where n/safe has the same
  ranks as n: probe 0.992 vs n 0.889. The probe separates choices within a level beyond what n alone does; part of
  that is the surface-cell effect on the choice.
- **What the descriptives don't show.** None of this makes probe_clean a risk-preference variable. G4 is an
  instrument gate (PREREG section 6).

These numbers were first seen on a trial build in the scratchpad during development, before registration. They gate
nothing and no rule depends on them.
