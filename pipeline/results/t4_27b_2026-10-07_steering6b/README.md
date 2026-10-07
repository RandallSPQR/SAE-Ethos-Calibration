# Item 6b steering, 27B (gate rules 2026-10-06.1): G4 = NOT_EVALUABLE (grid edge), all instrument checks passed

Pre-registration: `analyze/PREREG_ITEM6B_STEERING.md`. Frozen offline vectors: `results/t4_27b_2026-10-06_item6b_vectors/`.
Pod `4acahnxo0yybfc` (2 x A100 SXM, EUR-IS-1), code 573eb4b, 14:01:37-15:38:01 UTC 2026-10-07 (96 min), **$5.11**,
terminated after copy-back (0 pods). Created on the tenth stock window (stock waits 2026-10-06 15:40 to 2026-10-07 14:01,
nothing billed). Offline re-score (`probe.steer6b_analyze`) reproduces the pod's verdict; the only change is the NOT_EVALUABLE
reason text, which the pod printed with a stale "<= 0.4" (cosmetic, fixed in probe/steer_exact.py; no number changes).

## Instrument checks: all passed
Overlap 0 on every evaluation set (630 training prompts); batch gate; path check relative and absolute; lambda-0 at safe 70
(exact 101.1 vs served 100.3 +- 6.1); lambda-0 sampled agreement through the KV decode loop (native and A/B); manipulation
floor (acc(0) = 1.00, N = 108). The timing probe projected 72 min against a 94-min budget, so no fallback was needed: every
arm ran.

## D (the counterbalanced A/B CAA)
sd_v 507; worst-dimension push of a 1-sd step 2.00 (below the on-manifold STOP at 2.93); low-variance share 0.038; letter
direction removed share 0.0006 (counterbalancing cancelled it); cos(D, D_word) 0.33. Every strength +-0.25..+-4 is ELIGIBLE
for D: coherent, the manipulation check passes (accuracy 0.99-1.00, stated amount 69.7-70.0), >= 12/16 placebos intact.

## Why NOT_EVALUABLE
G4 needs every surface cell's switching point inside the grid (n = 10..180) for D and for all 16 covariance-matched placebos
at +-k*. At the evaluation level (safe 70) the unsteered safe_first/points cell already sits at sp 170.6 (safe_first/dollars
135, safe_first/tokens 115; risky_first cells 72-78). Any push toward the safe option moves that cell past 180, and some
placebos push hard: placebo_cov1 at -0.25 sd already takes safe_first/points off the grid. So no k <= 2 has every cell
defined for all 17 vectors, and the registered rule returns NOT_EVALUABLE. The relabeling cross-check is not evaluable
(no k*).

## Descriptive (gates nothing): D is a weak dial relative to natural-variance directions
Symmetric shift in mean P(risky) over the 210 evaluation prompts, P(+k) - P(-k) (`descriptive_dP.json`):

| k (sd) | D | cov placebos: median abs / max abs | D's rank by abs among D + 16 cov | isotropic (mean of 4) | n-direction | Fan |
|---|---|---|---|---|---|---|
| 0.25 | +0.010 | 0.035 / 0.066 | 13 / 17 | -0.000 | +0.051 | +0.049 |
| 0.5 | +0.019 | 0.068 / 0.155 | 13 / 17 | -0.000 | +0.117 | +0.107 |
| 1 | +0.038 | 0.161 / 0.266 | 13 / 17 | +0.000 | +0.231 | +0.223 |
| 2 | +0.075 | 0.313 / 0.582 | 14 / 17 | +0.000 | +0.456 | +0.436 |

At matched natural sd, a random covariance-matched direction typically moves the choice four times as much as D, and the
n-direction and Fan arms six times as much; isotropic directions do nothing. Had G4 been evaluable, criterion 3 (D beats
every placebo) would not have been met. The n-direction arm moves the choice while the stated stake stays 70 (manipulation
accuracy 0.99-1.00), so the shift is not a misreading of the stake.

Cost: item 6 ~$7.4 + item 6b $5.11 = **~$12.5** (item 6 cap $11.13 and item 6b cap $7.95 each held).

## Post-hoc additions (Randall, 2026-10-07; descriptive, no gate)
- **Per-cell baselines and distance from the grid edges** (`cell_baselines.json`). The edge failure was predictable from
  run 2: at safe 70 its served per-cell switching points put safe_first/dollars at 171 and safe_first/points at 160 (9 and
  20 from the top of 10-180). The exact lambda-0 readout here: safe_first/points 170.6 (9), safe_first/dollars 135 (45),
  safe_first/tokens 115 (65); risky_first cells 72.5-77.5 (62-68 from the bottom).
- **D's Delta log-odds rank among the 16 covariance-matched placebos, per cell** (`posthoc_dlogodds_rank.json`; POST HOC).
  Items: per cell, those with lambda-0 untruncated-softmax P(risky) in [0.1, 0.9], else the two nearest log-odds 0.
  Statistic: mean logit P(+k) - logit P(-k). D ranks 12/17 in the three risky_first cells and 13/17 in the three
  safe_first cells at every k (0.25, 0.5, 1, 2). Its Delta is about a quarter to a third of the placebos' median |Delta|,
  and far below their 95th percentile: at 1 sd, D +3.2 to +4.4 vs the placebo median 10.6-15.6 and p95 16.2-27.6.
  - A first pass on the served (top-p) readout was discarded: top-p returns exact 0/1 when an option leaves the
    nucleus, so log-odds hit the clip (18.42 = 2 x 9.21 everywhere).
  - The risky_first cells are near-steps (P 0.03 at n = 70, 0.98 at n = 75), so no grid item fell in [0.1, 0.9]. Both
    findings feed STEERING_PROTOCOL_V2.
- **Manipulation check limit:** it passed for the n-direction arm (0.99-1.00, stake stated as 70) while that arm moved
  P(risky) by +0.23 at 1 sd. It cannot detect magnitude and is a parser floor only.
