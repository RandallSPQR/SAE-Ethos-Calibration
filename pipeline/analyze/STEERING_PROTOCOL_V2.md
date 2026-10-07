# Steering protocol v2 — DRAFT (model-agnostic; not registered)

Status: **DRAFT** (2026-10-07; Randall's direction after items 6 and 6b). Pod spending on steering is parked until a candidate
variable (e.g. grader-belief, pressure) is designed; that variable's own pre-registration instantiates this protocol and
fills the values it leaves to per-model calibration. Nothing here has run.

**The principle: fix the procedures, not the values.** Every number that depends on the model (where its switching points
sit, how steep each cell is, how large a natural step is) is set by a rule stated here and applied by code at a calibration
step, then logged and frozen before any steered output. No value is tuned to Gemma.

## 1. What items 6 and 6b taught (the reasons for each rule below)

| lesson | evidence | rule it produces |
|---|---|---|
| A logistic decoding filter is not a steering direction | item 6: probe_clean (w / sd) put 74-92 % of its norm on the 10 % lowest-variance dims and collapsed the answer at every strength | strength in natural-sd units; an on-manifold check before any sweep (section 5) |
| The covariance-matched null is strong | item 6b: at matched natural sd, a random covariance-matched direction moved P(risky) about 4x as much as D (D ranked 13/17 at every k); isotropic placebos moved nothing | the null is a RANK test against N >= 40 covariance-matched placebos; isotropic is secondary (section 6) |
| A fixed stimulus grid fails at the edges | item 6b: at safe 70 one cell's baseline switching point sat 9 tokens from the grid top (run 2 already showed 9-20); one placebo pushing one cell off-grid made the gate unevaluable | per-cell calibration of the grid to the cell's own baseline (section 3); switching points censored, never "not evaluable" (section 4) |
| Cells differ more than any steering effect | the order x unit cells' baseline switching points at safe 70 ran 72-171 | every readout is measured against its own cell's baseline; cells are random effects (section 4) |
| Some cells are near-steps | item 6b: risky_first cells jump from P 0.03 (n = 70) to 0.98 (n = 75); no grid item fell in [0.1, 0.9] | indifference items placed by bisection over integer n, not on a fixed grid (section 3) |
| The top-p readout saturates | item 6b: the served (top-p 0.95) readout returns exact 0 / 1 when an option leaves the nucleus; log-odds then hit the clip | the primary log-odds readout is the untruncated softmax; the served readout is reported (section 4) |
| A restated-stake check cannot see magnitude | item 6b: steering along the n direction (magnitude by construction) shifted P(risky) by +0.23 at 1 sd while the model still stated the stake as 70 | the manipulation check is a parser floor only (section 7) |

## 2. Prompt sets
- Vector-construction prompts and evaluation prompts are disjoint by item key; the overlap is computed and logged, and must be 0.
- Calibration (section 3) uses the evaluation level(s) only.

## 3. Per-model calibration at lambda = 0 (no steering; fixed rule, run on the pod before anything steered)

For each cell c (the surface factorial, e.g. order x unit) at the evaluation level:
1. **Outer range.** Integer stimulus n in a declared outer range [n_min, n_max] (for the lottery: [1, 10 x safe]).
2. **Bisection.** Using the exact untruncated-softmax P(risky) at the registered temperature, find by bisection over
   integers the n at which baseline P = 0.10, 0.25, 0.50, 0.75, 0.90: n10_c, n25_c, n50_c, n75_c, n90_c (the smallest n
   with P >= the target). A deterministic forward per probe; about 8 forwards per target per cell.
3. **Grid.** The cell's descriptive grid is G = 15 integers evenly spaced over [n10_c, n90_c] (rounded, deduplicated),
   so every cell's grid spans its own baseline P from 0.1 to 0.9.
4. **Calibrated items.** The cell's primary items are {n25_c, n50_c, n75_c} (the indifference item and its neighbors).
   A near-step cell (n90_c - n10_c < 3) is flagged "step"; its items are {n50_c - 1, n50_c, n50_c + 1}.
5. **STOP rules.**
   - A cell whose baseline P never reaches 0.10 or 0.90 inside [n_min, n_max] is "saturated": reported and excluded.
   - STOP if more than a third of the cells saturate. Also STOP if any non-saturated, non-step cell's switching point
     falls outside the middle of its own grid: (n50_c - n10_c) / (n90_c - n10_c) outside [0.2, 0.8] (a baseline curve
     too asymmetric for a symmetric readout).
6. **Freeze.** Every calibrated n and grid is logged and hashed before any steered forward.

## 4. Readout

- **Primary: the shift in log-odds of the risky choice at the calibrated items.** For vector v at strength k:
  - per cell, Delta_c(v, k) = mean over the cell's calibrated items of [logit P(+k) - logit P(-k)], with untruncated
    softmax P at the registered temperature, clipped to [1e-6, 1 - 1e-6];
  - pooled: mu(v, k) = the random-effects mean over cells (cells as random effects; DerSimonian-Laird, with the
    within-cell variance from the item spread), with its 95 % CI.
- **Served (top-p) readout:** reported beside the primary. It is not used for log-odds, because it saturates at exact
  0 / 1.
- **Switching points:** descriptive. Per cell, on the calibrated grid; a curve that leaves the grid is CENSORED (reported
  as "< n10_c" or "> n90_c"), never "not evaluable".

## 5. Strength, eligibility and the on-manifold check (as item 6b, kept)

- **Strength unit:** k x sd_v (the natural projection sd of each vector, placebos included), an absolute displacement at
  every position of the steered layer. k in {+-0.25, +-0.5, +-1, +-2}.
- **The primary strength is k = 1 sd, fixed in advance.** If +-1 is ineligible, the largest eligible k < 1 is used, and
  that substitution is reported.
- **Eligibility of +-k, fixed before data:**
  - the target is coherent at both signs: untruncated option mass >= 0.95, ppl ratio <= 2.0, repeated 4-grams <= 0.25;
  - at least 80 % of the covariance-matched placebos have option mass >= 0.95 at both signs.
- **On-manifold STOP:** before any sweep, STOP if the target's worst-dimension push of a 1-sd step exceeds the isotropic
  placebos' range.

## 6. Null: a rank test

- **Placebos:** N covariance-matched placebos (x ~ N(0, S) from the vector-construction activations), each scaled by its
  own sd. **N >= 40, fixed in the instantiating pre-registration**, so that a 95th percentile is defined by at least two
  order statistics.
- **Test:** the target passes at the primary k iff
  - mu(target, k) has the expected sign, AND
  - |mu(target, k)| > the 95th percentile of |mu(p, k)| over the N placebos.

  Equivalently, its rank by |mu| among the N + 1 vectors is within the top 5 %.
- **Isotropic placebos:** secondary, reported (on this model they do nothing).
- **Per-cell ranks:** reported, descriptive.

## 7. Checks that gate the instrument, not the claim

- Instrument checks, each a STOP on failure:
  - the library pins;
  - option ids;
  - the batch gate;
  - the hook-path check (relative and absolute);
  - lambda-0 exact vs served agreement;
  - lambda-0 sampled agreement through the decode loop (the first-token property);
  - the overlap (0).
- **Manipulation check: a parser floor only.** At lambda = 0 the restated-stake accuracy must be >= 0.80, or the check
  (and the run) is NOT_EVALUABLE. Steered accuracy is reported. It is not a control for magnitude: a restated stake is
  copied from the prompt (item 6b, section 1).

## 8. Confirmatory test (template; the instantiating pre-registration names the vector and the layer)

**PASS** iff all of:
- the instrument checks passed;
- the primary k is eligible;
- the rank test of section 6 passes at the primary k.

**FAIL** otherwise. NOT_EVALUABLE only when an instrument check fails or no k is eligible, never because of the grid
(section 4). Everything else is descriptive, including:
- the other strengths;
- the per-cell ranks;
- the switching points (censored);
- the served readout;
- the isotropic comparison.

## 9. Cost shape (for pricing an instantiation)

| block | forwards (per vector set of 1 target + N = 40 placebos, 6 cells) |
|---|---|
| calibration (bisection, 5 targets x ~8 probes x 6 cells) | ~240 |
| primary readout (3 items x 6 cells x 41 vectors x 8 signed strengths) | ~5,900 |
| descriptive grids (15 x 6 cells x 41 x 8) | ~29,500 (optional; the first to drop on time) |

At the measured 0.048 s per forward on 2 x A100 (fp32 + TF32), the primary readout is about 5 minutes. A full
instantiation with coherence and the instrument checks fits well under an hour.

## 10. What stays from 6b
- The exact first-token readout.
- The KV-cached decode loop and pins.
- Absolute SD units.
- Counterbalanced construction where a vector is built from answer contrasts.
- The disjointness rule.
- The on-manifold STOP.
- The coherence rule.
- Resumable drivers with logged conditions.
- Offline re-scoring of everything committed.
