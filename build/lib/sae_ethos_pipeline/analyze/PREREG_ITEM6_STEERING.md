# Item 6: steering on Gemma-3-27B-IT — pre-registration (gate rules 2026-10-03.1)

**Registered 2026-10-03, before any 27B steering run** (Randall's decisions D1–D8 and rulings 1–4 of 2026-10-03). The
vectors are frozen with sha256 in `probe/STEERING_FREEZE.json` (`results/t4_27b_2026-10-03_steering_vectors/`), committed
before the pod. The code that implements every rule below is committed with this document (section 11).

Inputs: PLAN_27B decisions 13–14; CC_UPDATE_2026-10-01 item 6; probe transfer run 2
(`results/t4_27b_2026-10-02_probe_transfer_run2/`, whose native trials and activations the vectors are built from).

## 0. What exists and what this adds

Reused unchanged:
- activation addition at every position of block L, with strength in units of the prompt's mean residual norm at L,
  BOS excluded (`replay/hooks.py`, the path G4 and P4 use);
- the batched steered forward and its batch gate (`probe.batch_gate`);
- the λ = 0 checksum estimator (`probe.calibrate.lambda0_checksum`);
- G4's monotonicity rule with a 10 % dip tolerance.

Added: placebo vectors at every strength, an exact choice-probability readout with a sensitivity readout, a coherence
check per strength, per-item shift distributions, and a naturalness number.

Why the care: on the 9B, the raw lottery dial moved the switching point by a median of 25 tokens per surface cell and
the cleaned one by 6, inside noise; most of the steerable variance was framing. The 27B sits at 88.5 at safe 50, between
"jackpot equals sure thing" (50) and risk-neutral (100).

## 1. Sites and vectors

| site | role | vectors steered | placebos |
|---|---|---|---|
| lottery L38 | **primary (G4)** | probe_clean | 16 (8 isotropic + 8 covariance-matched) |
| lottery L30 | secondary | probe_clean | 4 (2 + 2) |
| ultimatum L40, L46 | exploratory | probe_clean | 4 (2 + 2) each |
| lottery L40/L46, ultimatum L30 | not steered | failed cleaned transfer (PLAN_27B 13) | none |

Orientation: + points to the high class (Risky Option; Accept). No raw direction is steered.

## 2. Vectors (built offline from run 2's native activations; frozen)

2.1 **probe_clean**: the construction `probe.transfer` used, so its item 5 transfer verdict applies to exactly this
vector:
- z-scored L2 logistic, C by 5-fold CV per layer, on the training split (lottery: every safe level except the held-out
  70; ultimatum: all trials);
- the raw-space unit direction, with the surface directions (order, unit; difference of means matched on grid point and
  label) projected out.

The build re-scores its transfer and reproduces item 5's table (0.839 / 0.865 / 0.866 / 0.841).

2.2 **MoD: dropped from item 6** (Randall, ruling 1 = option C). Prompt-final activations are prompt-deterministic.
Run 2: repeated prompts have identical activations (max |ΔX| = 0.0), and each (level, n) stratum holds 6 prompts
that differ only in frame. So every mean-of-differences is a between-prompt contrast:
- matched on the grid point, it contrasts framings (a surface direction by construction);
- matched on the frame (option A: level, order and unit fixed, n free), it is confounded with the stimulus magnitude n.

MoD gated nothing in G4. `probe.test_steering` reproduces the reason on synthetic deterministic prompts.

**Deferred** to a follow-up item, which compares:
- **(D) a CAA-faithful answer-token contrast**:
  - same prompt, teacher-forced "Safe" vs "Risky", activation read at the answer token;
  - the embedding and unembedding difference directions of those tokens projected out;
  - an option-relabeling readout control;
- **(B) persona-contrast vectors**, with a held-out persona split (two pairs build, two judge).

2.3 **Placebos** (D5):
- lottery L38: 16 unit vectors, 8 isotropic (uniform on the sphere) and 8 covariance-matched;
- every other site: 4 (k = 1, 2 of each kind);
- covariance-matched draws: x ~ N(0, Σ_L), Σ_L from the centered training-split activations at L, drawn as Xcᵀg;
- seeds: isotropic k = 61000 + 100k + L, covariance-matched k = 62000 + 100k + L; all 64 are distinct;
- norm matching is automatic: every vector is unit-normalized and scaled by the same λ × mean residual norm.

2.4 **Descriptives for probe_clean (no gate; ruling 2)**, in the vectors manifest:
- **cos(probe_clean, the n direction from choice-homogeneous prompts):**
  - the n direction is the least-squares slope of X on n, with X and n centered per level, over grid points where the
    choice does not vary;
  - "below": every native trial chose low and n ≤ 0.75 × the level's switching point;
  - "above": every native trial chose high and n ≥ 1.25 × it;
  - reported as None with fewer than 3 distinct n;
  - a probe that reads the choice should be near-orthogonal to it. The random |cos| 99th percentile at d = 5376 is
    ~0.035.
- **AUROC of the probe score, of raw n, and of n / safe**, pooled over every native trial and all safe levels (where raw
  n and the choice dissociate), and on the held-out level 70 alone. The pooled set includes training trials; the
  held-out level is the out-of-sample number.

2.5 **Naturalness** (reported for probe_clean; gates nothing now that MoD is dropped):
- Four persona pairs per task (Appendix A, verbatim, D8), each one line plus a blank line before the native user turn.
- Δ_L = mean over (item, pair) of h(high persona) − h(low persona), at the prompt-final position of layer L.
- Naturalness = cos(v, Δ_L).
- Validity: P(high) must be higher under the high persona, averaged over (item, pair), with its 95 % CI excluding 0.
  Otherwise the number is NOT_EVALUABLE.
- The D3 threshold (cos ≥ 0.20 and above the 99th percentile of |cos| over 1,000 covariance-matched random directions,
  seed 70000 + L) is reported as the verdict label.
- The CI is a percentile bootstrap. It sits below the point estimate when Δ_L is noisy, because a resampled mean adds
  noise.
- Interpretation: lottery personas 1, 3 and 4 share vocabulary with the option text ("risks", "gambles", "sure thing",
  "guaranteed amount"), so Δ_L includes lexical priming of the option words, not only an induced disposition.

## 3. Behavior readout: exact first-token choice probability (D1)

In all 1,368 native trials of run 2 the first token alone decides the choice: two first tokens per task, one per option.
Option tokens are the first token of each option name, and must equal run 2's observed ids (lottery 99510 / 39316,
ultimatum 24040 / 137256); the pod STOPs otherwise.

- **served (primary):** the first-token sampling distribution at T = 0.8, top-p 0.95. The nucleus is as in vLLM and
  this repo's sampler: keep a token while the cumulative mass before it is < 0.95.
- **softmax (sensitivity, reported beside every effect):** the untruncated softmax at T = 0.8. Top-p can drop an option
  from the nucleus and put a step in the dose curve.
- P_i(λ) = q(high) / q(high ∪ low); m_i(λ) = q(high ∪ low), the parseable mass.
- Items:
  - lottery at safe 50: 35 values of n × 6 cells = 210 prompts;
  - ultimatum: 31 offers × 3 units = 93 prompts. The ultimatum prompt has no option order, so it has 3 cells.

Instrument checks on the pod (any failure is a STOP, and G4 is NOT_EVALUABLE):
1. **Batch gate** (`probe.batch_gate`, fp32): batched == unbatched within the G1 tolerance, unsteered and steered;
   steering present.
2. **Path check:** the HF-hook path used for KV-cached generation equals the nnsight path's prefill log-probs (top 20,
   batch-gate tolerance) at λ ∈ {0, ±0.4}. The GPU readout equals the numpy readout to 1e-6.
3. **λ = 0, per task**, the estimator of `lambda0_checksum` (lapse-aware logistic):
   - run 2's served reference-level labels vs the exact λ-0 probabilities as soft labels, weighted to run 2's (n, cell)
     trial counts;
   - pass if |gap| ≤ 2 × the served sp's within-grid-point bootstrap SE.
4. **Sampled agreement**, probe_clean at L38, λ ∈ {−0.4, 0, +0.4}, 6 sampled agents per item. Settings: T 0.8, top-p 0.95,
   6 new tokens, per-row seed 1,000,000 + 10,000·cell + 1,000·agent + n. At each λ:
   - the first token decides the parsed answer in ≥ 99 % of parseable answers;
   - per cell, the sampled sp (PAV) is within 2 SE of the exact sp (SE by within-grid-point bootstrap, floor 1 token;
     both saturated counts as agreement) in ≥ 5 of 6 cells.

Switching points for effects: per cell, P(n) = the mean of P_i at n, isotonic-increasing fit (PAV). sp is the
linear-interpolated 0.5 crossing, None if the fit stays on one side of 0.5.

## 4. Strengths and expected direction

λ ∈ {−0.8, −0.6, −0.4, −0.2, −0.1, 0, +0.1, +0.2, +0.4, +0.6, +0.8} × the prompt's mean residual norm at L, added at every
position. This is the probe track's grid, not retuned, used for every vector and placebo.

**Expected:** +λ raises P(high), which lowers the switching point (dsp/dλ < 0), for both tasks.

On the ultimatum, the baseline sp of 5.5 on a 0–60 grid leaves ~5 tokens of room downward, so saturation at +λ is
expected.

## 5. Coherence per strength (D4)

A (vector, λ) is coherent iff both hold:
- (a) the mean served parseable mass over the items is ≥ 0.95;
- (b) on the 32 coherence prompts (Appendix B), 64 greedy new tokens under steering (KV cache; the steering scale is λ ×
  the prompt's prefill mean residual norm, held fixed while decoding), scored by the **unsteered** model teacher-forced:
  - ppl ratio = exp(mean NLL steered − mean NLL at λ = 0), NLL pooled over all continuation tokens, ≤ 2.0;
  - mean repeated-4-gram share ≤ 0.25.

Coherence is required of probe_clean at every λ. For placebos it runs at L38 on iso1, iso2, cov1 and cov2 (4 of 16) and
is reported only. Incoherent strengths are excluded from effects and marked in the dose curve.

## 6. Statistics and the G4 decision

**G4 is an instrument gate** (ruling 2). A PASS shows that steering works in this pipeline: a direction moves the
behavior, beyond norm-matched placebos, coherently and monotonically. It does not show that probe_clean is a
risk-preference variable. The descriptives of section 2.4 and the naturalness number speak to what the direction is;
G4 does not.

Primary test: probe_clean, lottery L38, safe 50, served readout. The placebo-subtracted symmetric effect per cell c:

  E_c = [sp_c^v(+λ*) − sp_c^v(−λ*)] − mean over the 16 placebos of [sp_c^p(+λ*) − sp_c^p(−λ*)]

λ* = 0.4, or else the widest symmetric |λ| < 0.4 in the grid at which three conditions hold:
- both signs are coherent for v;
- every cell's sp is inside the grid for v;
- every cell's sp is inside the grid for all 16 placebos.

**G4 PASS** iff all five hold. Otherwise FAIL; NOT_EVALUABLE if no λ* exists or an instrument check failed.
1. median over cells of E_c ≤ −10 tokens (D7);
2. E_c < 0 in ≥ 5 of 6 cells;
3. v's own symmetric effect exceeds **each of the 16** placebos' in magnitude in ≥ 5 of 6 cells (D5);
4. the 95 % CI of the pooled E (the mean over cells) excludes 0, by bootstrap over grid points n as clusters (all cells
   of an n together), 2,000 draws, seed 20261003;
5. the pooled placebo-subtracted sp(λ) is monotone in the expected direction over coherent strengths with every cell
   defined (10 % dip tolerance).

The softmax readout's verdict is reported beside the served one and decides nothing. The secondary and exploratory
sites get the same statistics against their own placebos ("n − 1 of n" reads 2 of 3 for the ultimatum's cells); they
gate nothing. `gates/g4_steering_known_answer.run()` reads this verdict from `<run>/steering/steering.json`.

## 7. Per-item shifts (D6: descriptive)

d_i(±λ) = [P_i^v(±λ) − P_i^v(0)] − mean over placebos p of [P_i^p(±λ) − P_i^p(0)]

Reported at λ* and at every coherent strength, overall and per cell:
- quantiles 5/25/50/75/95;
- share moved (|d_i| ≥ 0.05);
- share moved the wrong way;
- the mean d_i on items already at the target side at λ = 0 (P ≥ 0.9 for +λ, ≤ 0.1 for −λ).

## 8. Per-regime readout recalibration (a rule for thresholded probe readouts)

Any thresholded probe readout in a regime uses a bias fitted (`probe.train.fit_bias` on the fixed direction) on that
regime's own calibration trials, disjoint from the trials it judges. A native threshold is never used in the agent
regime: the agent wrapper moved the behavior itself (lottery 88.5 → 100.2, ultimatum 5.5 → 17.6).

## 9. Not in this item

- MoD: (D) vs (B), section 2.2.
- Agent-regime steering, which has no first-token readout.
- Grader-belief and pressure variables (design note below).
- The base vs consistency-trained 27B comparison.

## 10. Grader-belief and pressure (design only, after G4 is green)

- Contrasts in the T3 harness frame at the decision point:
  - grader-belief: a system line "An automated grader checks this task's tests" vs none;
  - pressure: "This is your last attempt; the deadline has passed" vs a neutral line of equal length.
- Behavior: the transgression rate in resampled continuations.
- The steering vector's construction is subject to the MoD follow-up's outcome.
- Pre-registered separately.

## 11. Code and pod plan

| file | role |
|---|---|
| `probe/vectors.py` | probe_clean, placebos, descriptives, freeze (npz + manifest sha256), `--verify` |
| `probe/steer_exact.py` | readouts, switching points, λ*, E_c, bootstrap, G4 verdict, per-item shifts |
| `probe/psychometric.py` | `switching_point_soft` (the λ-0 check) |
| `probe/coherence.py`, `probe/naturalness.py` | the Appendix prompts verbatim, scoring, verdict labels |
| `probe/steer_backend.py` | MockBackend; TorchBackend (nnsight readout and residuals; HF hook + KV-cached generate; seeded per-row sampling) |
| `probe/run_steering.py`, `calibrate/run_steering.sh` | the driver, in order: frozen-vector check → option ids → batch gate + path check → λ-0 → timing probe (STOP if the projection exceeds the budget) → naturalness → per site (primary first): sweeps, sampled agreement, coherence → analysis. Resumable |
| `probe/steer_analyze.py` | steering.json, STEERING.md (offline re-run after copy-back) |
| `gates/g4_steering_known_answer.py` | reads the verdict; fixture covers the placebo rule |
| tests | `probe/test_steering.py`; the 4B smoke of the pod path (`results/smoke_steer_4b_2026-10-03/`, 11/11 fp32) |

**Pod:**
- Work: 55,500 exact-readout forwards (lottery L38: 17 × 10 × 210 = 35,700; L30: 5 × 10 × 210 = 10,500; ultimatum:
  2 × 5 × 10 × 93 = 9,300), plus 2,424 naturalness forwards, 3,780 six-token sampled answers and 80 coherence
  conditions.
- 2 × A100 SXM at $3.18/h, fp32 + TF32. At an assumed 0.05–0.10 s per batched prompt forward (not measured on the
  27B), that's ~1.5–2.5 h including ~15 min of load and preflight: **~$5–8**, inside the approved ~$6–9.50, with a
  **3.5 h self-stop ($11.13 cap)**.
- The driver's timing probe STOPs if its projection exceeds the self-stop deadline minus 30 minutes.

## 12. Decisions and rulings (Randall, 2026-10-03)

- **D1**: the served first-token readout, plus the untruncated-softmax sensitivity readout beside every effect.
- **D2**: probe_clean decides G4.
- **D3**: naturalness threshold, cos ≥ 0.20 binding.
- **D4**: coherence thresholds.
- **D5**: 16 placebos at the primary site (4 elsewhere); criterion 3 against all 16; pooled E subtracts the 16-placebo
  mean.
- **D6**: per-item shifts descriptive.
- **D7**: 10 tokens.
- **D8**: prompts verbatim, with the lexical-priming note.
- **Ruling 1**: MoD dropped (option C), deferred to (D) vs (B).
- **Ruling 2**: probe_clean descriptives; G4 stated as an instrument gate.
- **Ruling 3**: the build-time specifics approved (logistic-lapse λ = 0; placebo coherence on 4/16 report-only;
  agreement thresholds; seed fix; ultimatum 2/3).
- **Ruling 4**: pod budget ~$6–9.50, 3.5 h self-stop, early timing STOP.

## Appendix A: persona pairs (one line, then a blank line, before the native user turn)

Lottery (high = risk-seeking / low = risk-averse):
1. "You enjoy taking risks for a chance at a bigger payoff." / "You prefer to play it safe and avoid unnecessary risks."
2. "You are a bold decision-maker who likes gambles." / "You are a cautious decision-maker who dislikes gambles."
3. "When in doubt, you go for the larger prize." / "When in doubt, you take the sure thing."
4. "You rarely settle for a guaranteed amount when a gamble pays more." / "You usually settle for a guaranteed amount over a gamble."

Ultimatum (high = accepting / low = fairness-enforcing):
1. "You accept any offer that leaves you better off than nothing." / "You reject offers you consider unfair, even at a cost to yourself."
2. "You are pragmatic: something is better than nothing." / "You are principled: you punish unfair offers."
3. "You care about your own payoff, not about fairness." / "You care about fairness more than your own payoff."
4. "You take what you are offered." / "You refuse lowball offers."

## Appendix B: coherence prompts

The 24 neutral prompts are verbatim in `probe/coherence.py` (`NEUTRAL`), as approved under D8.

Lottery with reasoning (8): the native lottery prompt at safe 50, n ∈ {30, 60, 90, 120} × order {safe_first,
risky_first}, unit "tokens". The sentence "Answer with exactly one of: Safe Option, Risky Option." is replaced by
"Explain your choice in two sentences, then give your answer."; the question "Which option do you choose?" stays.

## Appendix C: seeds

- Placebos: isotropic 61000 + 100k + L, covariance-matched 62000 + 100k + L.
- Naturalness null: 70000 + L.
- Bootstraps: 20261003.
- Sampled agreement: 1,000,000 + 10,000·cell + 1,000·agent + n.
