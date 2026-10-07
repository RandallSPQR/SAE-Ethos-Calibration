# Steering protocol v2 — DRAFT (model-agnostic; not registered)

Status: **DRAFT, approved as the template with four edits** (2026-10-07; Randall): (1) the n-direction positive control and
the UNDERPOWERED verdict; (2) asymmetric cells excluded like saturated ones, STOP only if more than a third of cells are
excluded for any reason; (3) the test statistic is the unweighted mean of Delta_c, identical for every vector; (4) a
monotonicity check before bisection. Still unregistered. Pod spending on steering is parked until a candidate
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
| The null is strict enough to need a positive control | item 6b, post hoc: the n direction ranked 3/17 among D + 16 covariance-matched placebos at k <= 1 (5/17 at k = 2) and did not exceed their 95th percentile at any k, under either statistic (`results/t4_27b_2026-10-07_steering6b/posthoc_ndirection_rank.json`) | every instantiation runs the n direction through the same rank test; if it fails, a target's non-pass is UNDERPOWERED (section 8) |
| Placebos drawn from the task covariance carry the task's variables | 6b post hoc: a placebo's push along the stimulus direction explains R² 0.59 [0.38, 0.82] of the placebos' effects, and the line through the placebos predicts the positive control out of sample (+0.278 predicted vs +0.231 measured) | draw the null from task-free activations (primary: generic text) or project the known task directions out (secondary); the task covariance is descriptive (sections 6, 11) |

## 2. Prompt sets
- Vector-construction prompts and evaluation prompts are disjoint by item key; the overlap is computed and logged, and must be 0.
- Calibration (section 3) uses the evaluation level(s) only.

## 3. Per-model calibration at lambda = 0 (no steering; fixed rule, run on the pod before anything steered)

For each cell c (the surface factorial, e.g. order x unit) at the evaluation level:
1. **Outer range.** Integer stimulus n in a declared outer range [n_min, n_max] (for the lottery: [1, 10 x safe]).
1b. **Monotonicity check (before bisecting).** P(risky) at 12 log-spaced integers over [n_min, n_max]. The cell is
   monotone iff no step down between successive points exceeds 0.05 AND P(n_max) - P(n_min) >= 0.5. Otherwise it is
   flagged "non-monotone" and excluded (bisection assumes P rises with n).
2. **Bisection.** Using the exact untruncated-softmax P(risky) at the registered temperature, find by bisection over
   integers the n at which baseline P = 0.10, 0.25, 0.50, 0.75, 0.90: n10_c, n25_c, n50_c, n75_c, n90_c (the smallest n
   with P >= the target). A deterministic forward per probe; about 8 forwards per target per cell.
3. **Grid.** The cell's descriptive grid is G = 15 integers evenly spaced over [n10_c, n90_c] (rounded, deduplicated),
   so every cell's grid spans its own baseline P from 0.1 to 0.9.
4. **Calibrated items.** The cell's primary items are {n25_c, n50_c, n75_c} (the indifference item and its neighbors).
   A near-step cell (n90_c - n10_c < 3) is flagged "step"; its items are {n50_c - 1, n50_c, n50_c + 1}.
5. **Exclusions and the STOP rule.** A cell is flagged, reported and EXCLUDED when it is any of:
   - **non-monotone** (1b);
   - **saturated**: its baseline P never reaches 0.10 or 0.90 inside [n_min, n_max];
   - **asymmetric**: (n50_c - n10_c) / (n90_c - n10_c) outside [0.2, 0.8] (a non-step cell whose switching point sits
     outside the middle of its own grid).

   **STOP only if more than a third of the cells are excluded for any reason.** The included cells are frozen with the
   calibration.
6. **Freeze.** Every calibrated n and grid is logged and hashed before any steered forward.

## 4. Readout

- **Primary: the shift in log-odds of the risky choice at the calibrated items.** For vector v at strength k:
  - per cell, Delta_c(v, k) = mean over the cell's calibrated items of [logit P(+k) - logit P(-k)], with untruncated
    softmax P at the registered temperature, clipped to [1e-6, 1 - 1e-6];
  - **test statistic: T(v, k) = the UNWEIGHTED mean of Delta_c(v, k) over the included cells**, computed identically for
    the target, the positive control and every placebo;
  - descriptive: the random-effects mean over cells (cells as random effects; DerSimonian-Laird) with the Hartung-Knapp
    95 % CI.
- **Served (top-p) readout:** reported beside the primary. It is not used for log-odds, because it saturates at exact
  0 / 1.
- **Switching points:** descriptive. Per cell, on the calibrated grid; a curve that leaves the grid is CENSORED (reported
  as "< n10_c" or "> n90_c"), never "not evaluable".

## 5. Strength, eligibility and the on-manifold check (as item 6b, kept)

- **Strength unit:** k x sd_v, an absolute displacement at every position of the steered layer. k is one of
  {+-0.25, +-0.5, +-1, +-2}.
- **sd_v is the natural projection sd of each vector on ONE activation set:** the task EVALUATION activations (the
  prompt-final residual at the steered layer on the instantiation's evaluation prompts at lambda = 0, collected on the
  pod before any steered output). That set is the same for the target, the positive control and every placebo of every
  null. Item 6b measured sd on the training split; v2 does not.
- **The primary strength is k = 1 sd, fixed in advance.** If +-1 is ineligible, the largest eligible k < 1 is used, and
  that substitution is reported.
- **Eligibility of +-k, fixed before data:**
  - the target is coherent at both signs: untruncated option mass >= 0.95, ppl ratio <= 2.0, repeated 4-grams <= 0.25;
  - at least 80 % of the PRIMARY-null placebos have option mass >= 0.95 at both signs.
- **On-manifold STOP:** before any sweep, STOP if the target's worst-dimension push of a 1-sd step exceeds the isotropic
  placebos' range.

## 6. Null: a rank test

- **Placebos (null change of 2026-10-07, section 11; unregistered).** Each is scaled by its own natural sd on the task
  evaluation activations, the common unit for every vector (section 5). **N >= 40 per null, fixed in the instantiating pre-registration**,
  so that a 95th percentile is defined by at least two order statistics.
  - **PRIMARY null: generic-text covariance.** x ~ N(0, S_generic), drawn as Xgᵀg. Xg is the centered activations at the
    same layer and position (the prompt-final position of a single chat-formatted user turn) on a fixed neutral corpus,
    collected on the calibration pod:
    - **source:** `databricks/databricks-dolly-15k` at a pinned revision, the `instruction` field only (no context);
    - **filter:** instructions of 5-60 words that match none of the fixed keyword list (money, dollar, cost, price, pay,
      invest, bet, gamble, lottery, risk, risky, safe, guarantee, chance, probability, odds, win, lose, offer, accept,
      reject, token, point, reward, prize);
    - **selection:** the first 2,000 after a seeded shuffle (seed 95000).

    The same procedure applies to any model.

    **On-manifold diagnostics for every generic placebo, reported and counted:**
    - the worst-dimension push of a 1-sd step (in each dimension's own sd on the task evaluation activations);
    - the low-variance share (|v|² on the 10 % lowest-variance dimensions);
    - the massive-activation loading: |v|² on the dimensions whose mean |activation| on the task evaluation set exceeds
      100 × the median dimension's.

    The eligibility count is the number of generic placebos eligible at each ±k (option mass >= 0.95 at both signs),
    reported beside the test. A generic direction can be off the task manifold, so this count shows how much of the
    null survives.
  - **SECONDARY null: task covariance with the known task directions projected out.** x ~ N(0, S_task) as in item 6b;
    then, for each placebo, the known task directions are projected out geometrically (Gram-Schmidt) and the result is
    re-normalized:
    - the stimulus direction (the n slope and the frame-matched MoD; they are collinear, and both enter the basis);
    - the safe-level mean differences;
    - the cell (order × unit) mean differences.

    The n-push is 0 by construction. This replaces the residual task covariance of the first redraft, which halved the n
    content (median |n-push| 0.44 -> 0.21 n-sd) without removing it.
  - **DESCRIPTIVE comparison: the task covariance** (the item-6b null). It shows how far the choice moves along the
    task's own variance directions.
  - **Diagnostic, reported for every null:** each placebo's push along the stimulus direction for a 1-sd step,
    sd_p · cos(p, n) / sd_n, in the stimulus direction's own sd. On the 6b data it is median |0.44| for the task
    covariance; it is 0 by construction for the secondary null; for the generic null it is measured at collection.
- **Test:** a vector v passes at the primary k iff
  - T(v, k) has the expected sign, AND
  - |T(v, k)| > the 95th percentile of |T(p, k)| over the N placebos.

  Equivalently, its rank by |T| among the N + 1 vectors is within the top 5 %. With N = 40 the smallest attainable p is
  1/41 (rank 1 of 41).
- **Positive control:** the n direction (the within-level slope of the activations on the stimulus; expected sign +)
  goes through the same test, at the same k, against the same placebos, in every instantiation.
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

Verdicts, in this order:
- **NOT_EVALUABLE:** an instrument check failed, the calibration STOPped, or no k <= 1 is eligible for the target (the
  primary k is 1 sd, or the largest eligible k below it, section 5).
  Never because of the grid (section 4).
- **PASS:** the target passes the rank test of section 6 at the primary k.
- **UNDERPOWERED:** the target does not pass, AND the positive control does not pass either (or is ineligible) at the
  same k. The null was not shown to be beatable by a known magnitude direction, so the target's failure is uninformative.
- **FAIL:** the target does not pass while the positive control does.

Everything else is descriptive, including:
- the other strengths;
- the per-cell ranks;
- the switching points (censored);
- the served readout;
- the isotropic comparison.

## 9. Cost shape (for pricing an instantiation)

| block | forwards (per vector set of 1 target + the positive control + N = 40 placebos, 6 cells) |
|---|---|
| calibration (monotonicity 12 + bisection 5 targets x ~8 probes, x 6 cells) | ~310 |
| primary readout (3 items x 6 cells x 42 vectors (target, positive control, 40 placebos) x 8 signed strengths) | ~6,050 |
| descriptive grids (15 x 6 cells x 42 x 8) | ~30,240 (optional; the first to drop on time) |

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

## 11. The null, decided in stages (2026-10-07)

**Stage 1, the evidence (post hoc, descriptive; `results/t4_27b_2026-10-07_steering6b/posthoc_null_contamination.json`).**
For the 16 task-covariance placebos of 6b, each placebo's push along the stimulus direction (k · sd_p · cos(p, n) / sd_n)
explains **R² = 0.59 [0.38, 0.82]** (bootstrap 95 % CI) of the spread in their ΔP at k = 1 (0.59 at k = 2; Δlog-odds
0.56-0.58).
- **Out-of-sample prediction:** the line through the placebos predicts the positive control's own effect, **+0.278
  predicted vs +0.231 measured** (k = 1). The positive control is not among the 16 points that fit the line.
- **"The stimulus direction":** the n slope and the frame-matched MoD are collinear (cos 0.992 raw, 0.77 whitened, at
  L38); they are recorded as one direction.
- **Whitened cosines** explain less (0.17-0.26).
- **A random task-covariance direction** carries up to ±0.8 sd of push along the stimulus direction at k = 1.
- **Illustrative only:** with each placebo's stimulus-predicted part removed, the n direction ranks 1/17 at both k
  (raw 3/17 and 5/17). This is partly by construction: removing exactly the component the positive control is made of
  favors it.

Reading: the 6b null was largely a random mix of the task's own variables, magnitude included. "Random directions shaped
like this task's activation spread move the choice" stands; "any on-manifold push moves it" is not shown.

**Stage 2, the ruling (Randall).**
- The null is drawn from a covariance with the task taken out (section 6): primary generic text, secondary the task
  covariance with the known task directions projected out. The task covariance becomes a descriptive comparison.
- The positive control and the UNDERPOWERED rule stay.
- Rejected: accepting the null as it was (uninformative), and rescaling placebos to the target's norm (it changes the
  units after the fact and not what the placebos are made of).
- Not yet: changing the task.

**Still to see, at collection:**
- the generic null's n-push distribution;
- whether the positive control clears it.

If the positive control fails even the generic null, the task change returns to the table.

**Collection** piggybacks on the next calibration pod. No pod runs until a candidate variable is designed.

**Price of the generic-corpus collection.** 2,000 prompts of 5-60 words, one forward each, capturing the prompt-final
residual at one layer. At the measured 0.048 s per prompt that is about 2 minutes, plus a ~13 MB dataset download, run on
the calibration pod (model already loaded): about **$0.10-0.20 at $3.18/h**. As a standalone pod, add ~15 minutes of
preflight and load: about **$1**. The activations are 2,000 × 5,376 fp32 ≈ 43 MB.

**Earlier finding (kept for the record).** On Gemma-3-27B's lottery at L38, the n direction does not clear the
task-covariance null (rank 3/17 at k <= 1). That is why the null was changed.
