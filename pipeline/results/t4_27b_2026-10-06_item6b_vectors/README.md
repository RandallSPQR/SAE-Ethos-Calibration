# Item 6b offline vectors, FROZEN (gate rules 2026-10-06.1)

Built 2026-10-06 after the registration commit 4b6e129 by `python -m probe.vectors6b`. npz sha256 `0e36751d588b23dcb2708536149db73036f48289ba3c95dc6982e750735ab97d`
(also in `probe/STEERING6B_FREEZE.json`); an independent rebuild produced identical bytes. Lottery L38, run 2 native
training split (840 trials over 630 prompts). D and D_word (the CAA vectors) are built on the pod.

| vector | sd_v (the 6b unit) | worst-dimension push of a 1-sd step (dim sds) | low-variance share |
|---|---|---|---|
| n_direction | 2475.7 | 1.03 | 0.003 |
| fan | 1268.8 | 26.30 | 0.109 |
| placebo_cov1 | 2018.7 | 1.96 | 0.009 |
| placebo_cov2 | 1455.5 | 1.36 | 0.011 |
| placebo_cov3 | 1139.3 | 1.33 | 0.015 |
| placebo_cov4 | 2163.3 | 1.95 | 0.008 |
| placebo_cov5 | 2149.9 | 1.83 | 0.006 |
| placebo_cov6 | 2211.4 | 1.78 | 0.006 |
| placebo_cov7 | 1877.9 | 1.98 | 0.010 |
| placebo_cov8 | 1330.7 | 1.59 | 0.013 |
| placebo_cov9 | 1389.1 | 1.81 | 0.016 |
| placebo_cov10 | 2017.9 | 1.60 | 0.007 |
| placebo_cov11 | 1056.9 | 1.37 | 0.017 |
| placebo_cov12 | 2081.4 | 1.37 | 0.005 |
| placebo_cov13 | 1764.3 | 1.51 | 0.010 |
| placebo_cov14 | 1150.2 | 1.76 | 0.022 |
| placebo_cov15 | 1757.6 | 1.71 | 0.010 |
| placebo_cov16 | 2114.4 | 1.70 | 0.007 |
| placebo_iso1 | 46.4 | 2.93 | 0.098 |
| placebo_iso2 | 36.6 | 1.13 | 0.106 |
| placebo_iso3 | 20.9 | 1.11 | 0.098 |
| placebo_iso4 | 30.8 | 0.67 | 0.099 |

Isotropic range of the worst-dimension push: 0.67-2.93, the threshold of the pod's
on-manifold STOP for D (> 2.93). Descriptive notes: the n direction is on-manifold (1.03); Fan's z-space vector is not
(26.3), which matters only for reading the descriptive Fan arm, not for G4.
