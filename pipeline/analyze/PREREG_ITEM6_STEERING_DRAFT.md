# Item 6: steering on Gemma-3-27B-IT — design and pre-registration DRAFT

Status: **DRAFT, not registered.** Registration = Randall approves the open decisions (section 12); the rules are then
written to `gates/CHANGELOG.md` as gate rules 2026-10-03.1 and committed, and the vectors are built, hashed and
committed, all before any pod. Nothing here has touched steering data: no 27B steering run exists.

Inputs: Randall's item 6 decisions of 2026-10-03 (PLAN_27B decisions 13 and 14), CC_UPDATE_2026-10-01 item 6, probe
transfer run 2 (`results/t4_27b_2026-10-02_probe_transfer_run2/`).

## 0. What exists and what this adds

Reused unchanged: activation addition at every position of block L, strength in units of the prompt's mean residual
norm at L with BOS excluded (`replay/hooks.py`, the path G4 and P4 already use); the batched steered forward and its
batch gate (`probe.batch_gate`); the lambda = 0 checksum against the served model (`probe.calibrate`); per-surface-cell
effects (`probe.cell_effects`); G4's monotonicity rule with a 10 % dip tolerance (`gates/g4_steering_known_answer.coherent`).

Added: mean-of-differences (MoD) vectors, placebo vectors at every strength, a coherence check per strength, per-item
shift distributions, a naturalness check, and an exact choice-probability readout (section 3).

Why the care: on the 9B, the raw lottery dial moved the switching point by a median of 25 tokens per surface cell, and
the cleaned one by 6, inside noise; most of the steerable variance was framing (FINDINGS, run 3). The 27B sits at 88.5
at safe 50, between "jackpot equals sure thing" (50) and risk-neutral (100), so it may weigh the probability. That is
the model on which a cleaned dial could work, and this design has to be able to say no.

## 1. Sites and vectors

| site | role | vectors steered | not steered |
|---|---|---|---|
| lottery L38 | **primary (G4)** | probe_clean (decides G4), MoD_clean (side by side) | raw directions |
| lottery L30 | secondary | probe_clean, MoD_clean | raw directions |
| ultimatum L40, L46 | exploratory | probe_clean, MoD_clean | raw directions |
| lottery L40, L46; ultimatum L30 | none | none: failed cleaned transfer (PLAN_27B 13) | all |

Every site also gets four placebo vectors (section 2.3). Orientation: + points to the high class (Risky Option;
Accept) for every vector.

## 2. Vector construction (offline, $0, from run 2's native activations)

Training split: lottery = every safe level except the held-out 70 (as `probe.train`); ultimatum = all trials (one level).

2.1 **probe_clean**: as `probe.train`: z-scored L2 logistic, C by 5-fold CV per layer, the raw-space unit direction,
surface directions (order, unit; difference of means matched on grid point and label) projected out.

2.2 **MoD_clean**: within each stratum (level, n) where both choices occur, mean(x | high) − mean(x | low); average
over strata weighted by min(n_high, n_low); project out the same surface directions; unit-normalize. Matching on the
grid point is required: the unmatched class-mean difference is dominated by the number in the prompt (the risky
option is chosen at high n). Reported descriptively: cos(MoD_clean, probe_clean), and cos(unmatched MoD, the n
direction).

2.3 **Placebos**, per site, four unit vectors, seeds fixed here: two isotropic (uniform on the sphere; seeds 6100 + L,
6200 + L) and two covariance-matched (x ~ N(0, Σ_L), Σ_L from the training-split activations at L; seeds 6300 + L,
6400 + L). Norm matching is automatic: every vector is unit-normalized and scaled by the same λ × mean residual norm.

2.4 **Freeze**: all vectors go to `steering_vectors.npz` with their sha256 in the registration commit, before the pod.

### 2.5 Use gates for MoD vectors (before any steering with them)

a. **Transfer** (gate rules 2026-10-02.1, unchanged): the MoD_clean direction's agent-regime AUROC ≥ 0.70 with the 95 %
   cluster-bootstrap lower bound > 0.50. Offline on run 2's agent activations, $0, before the pod.

b. **Naturalness**: does the vector point where a prompt that induces the behavior moves the residual? Four persona pairs
   per task (Appendix A, fixed here), each a one-sentence line prepended to the native user turn. The prompt-induced
   shift Δ_L = mean over the reference-level items and the four pairs of h(high-persona) − h(low-persona) at the
   prompt-final position of layer L. Naturalness = cos(v, Δ_L), 95 % CI by bootstrap over items × pairs.
   - Validity: the personas must move the behavior. The exact P(high) (section 3) must be higher under the
     high persona, averaged over items, with a CI excluding 0. Otherwise the check is NOT_EVALUABLE: the prompt doesn't
     induce the behavior.
   - PASS (**decision D3**): cos(v, Δ_L) ≥ 0.20, and above the 99th percentile of |cos(Δ_L, r)| over 1,000
     covariance-matched random directions r.
   - Computed on the pod before the sweeps. A MoD vector that fails (a) or (b) isn't steered; its numbers are reported.
   - The probe vectors get the same naturalness number, reported but not gating, since their use gate is transfer,
     which they passed.

## 3. Behavior readout: exact first-token choice probability (**decision D1**)

In all 1,368 native trials of run 2 the first token alone decides the choice: each task has exactly two first tokens,
one per option (lottery: `Safe` 606 → low, `Risky` 514 → high; ultimatum: `Accept` 228 → high, `Reject` 20 → low).
So under steering, the behavior is read exactly from one forward per prompt:

  q = the first-token sampling distribution at T = 0.8, top-p 0.95 (the probe track's settings)
  P_i(λ) = q(high tokens) / q(high ∪ low tokens);  m_i(λ) = q(high ∪ low tokens), the parseable mass

Items: lottery at safe 50: 35 values of n × 6 surface cells (order × unit) = 210 distinct prompts. Run 2's 280
reference trials are 8 seeds over these 210 prompts. Ultimatum: 31 offers × 3 units = 93 prompts.

Why replace sampled agents:
- Sampling noise was the binding constraint on the 9B (2 agents per cell per grid point → NOT_EVALUABLE).
- Placebos at every strength become affordable.
- Per-item shifts become exact, not estimates.

Instrument checks on the pod (STOP on failure):
- **λ = 0**: the exact-readout switching point at safe 50 lies within 2 combined SE of the served vLLM curve from run 2
  (88.5). Same rule as `probe.calibrate.lambda0_checksum`.
- **Sampled agreement**: probe_clean at L38, λ ∈ {−0.4, 0, +0.4}, 6 sampled agents per item through the existing batched
  sampler. Per cell, the sampled switching point lies within 2 SE of the exact-readout one in at least 5 of 6 cells.
- **Batch gate** (`probe.batch_gate`) on the 27B before any batched forward is trusted.

Switching point from exact probabilities: per cell, the curve P(n) = mean of P_i over the cell's items at n,
isotonic-fitted (PAV, in the expected direction). sp is the linear-interpolated 0.5 crossing; None if it doesn't cross
inside the grid.

## 4. Strengths and expected direction

λ ∈ {−0.8, −0.6, −0.4, −0.2, −0.1, 0, +0.1, +0.2, +0.4, +0.6, +0.8} × the prompt's mean residual norm at L (BOS
excluded), added at every position. This is the probe track's existing grid (`run.yaml probe.lambda_sweep`), not
retuned. The same grid is used for every vector and placebo.

Expected direction: +λ raises P(high), which **lowers the switching point** (dsp/dλ < 0) for both tasks.

Ultimatum caveat: the baseline sp of 5.5 on a 0–60 grid leaves about 5 tokens of room downward. Its effect shows mainly
at −λ (more rejection), and saturation at +λ is expected, not a failure. The ultimatum is exploratory.

## 5. Coherence per strength

A (vector, λ) that fails coherence is excluded from effects. The dose curve is reported at every strength, with
incoherent ones marked.

a. **Parseable mass**: mean m_i(λ) ≥ 0.95 over the items. This mirrors P1's 5 % drop STOP.

b. **Free text**: 24 neutral prompts plus 8 lottery prompts that ask for two sentences of reasoning (Appendix B), with 64
   greedy new tokens under steering.
   - Path: KV-cached generation with a forward hook. The scale is λ × the prompt's mean residual norm at prefill, held
     fixed while decoding.
   - Path check before use: the hook path's last-position logits equal the nnsight path's at λ ∈ {0, ±0.4} on 8
     prompts, within the batch gate's tolerance.
   - Scores: the perplexity of the steered continuation under the **unsteered** model (teacher-forced), as a ratio to
     the λ = 0 continuation's; and the share of repeated 4-grams.
   - Coherent (**decision D4**): ppl ratio ≤ 2.0 and repeated-4-gram share ≤ 0.25.
   - Required for the steered vectors at every λ; for placebos, run at the primary site and reported.

## 6. Statistics and the G4 decision

Primary test: probe_clean, lottery L38, safe 50. Per surface cell c (6), vector v and strength λ, the switching point is
sp_c^v(λ). The placebo-subtracted symmetric effect at λ*:

  E_c = [sp_c^v(+λ*) − sp_c^v(−λ*)] − mean over the 4 placebos p of [sp_c^p(+λ*) − sp_c^p(−λ*)]

λ* = 0.4, or else the widest symmetric |λ| < 0.4 in the grid at which three conditions hold:
- both signs are coherent for v;
- every cell's sp is inside the grid for v;
- every cell's sp is inside the grid for every placebo.

This is `cell_effects`' fallback rule, extended to placebos.

**G4 PASS** iff all of the following hold (otherwise FAIL; NOT_EVALUABLE if no λ* exists or an instrument check fails):
1. median over cells of E_c ≤ −10 tokens: the expected sign, with G9's magnitude `g9_cell_effect_min` (**decision D7**).
2. E_c < 0 in at least 5 of 6 cells.
3. In at least 5 of 6 cells, v's own symmetric effect exceeds **every individual** placebo's in magnitude
   (**decision D5**).
4. The 95 % CI of the pooled E (the mean over cells) excludes 0. Bootstrap over grid points n as clusters (all 6 cells
   of an n together), 2,000 draws.
5. Dose curve: the pooled placebo-subtracted sp(λ) is monotone in the expected direction over the coherent strengths
   (G4's `coherent()`, 10 % dip tolerance).

Secondary and exploratory (MoD at L38; both vectors at L30; the ultimatum sites) get the same statistics. They are
reported side by side, MoD next to probe direction, and gate nothing.

## 7. Per-item shifts (reported for every vector, at λ* and at every coherent strength)

d_i(±λ) = [P_i^v(±λ) − P_i^v(0)] − mean over placebos p of [P_i^p(±λ) − P_i^p(0)]

Reported overall and per cell:
- quantiles 5/25/50/75/95;
- share moved, |d_i| ≥ 0.05;
- share moved the wrong way: the sign opposite to the expected one and |d_i| ≥ 0.05;
- the mean d_i on items already at the target side at λ = 0 (P_i(0) ≥ 0.9 for +λ, ≤ 0.1 for −λ), the ceiling and
  floor items.

Descriptive (**decision D6**: or a gate, wrong-way share ≤ 0.10).

## 8. Per-regime readout recalibration (a rule for readouts; item 6's measure is behavior)

Any thresholded probe readout in a regime uses a bias fitted (`probe.train.fit_bias` on the fixed direction) on that
regime's own calibration trials, disjoint from the trials it judges. A native threshold is never used in the agent
regime: the wrapper moved the behavior itself (lottery 88.5 → 100.2, ultimatum 5.5 → 17.6). For the agent regime, run 2's
agent trials are the calibration set. The span study names its own calibration split in its pre-registration.

## 9. Not in this item

- **Agent-regime steering**: the answer comes after ~100 tokens of reasoning, so there's no first-token readout; it needs
  sampled generation. That's a later pod, designed after G4.
- **Base vs consistency-trained 27B**: deferred.

## 10. Grader-belief and pressure: candidate causal variables for Arm A (design only, after G4 is green)

- Contrasts in the T3 harness frame at the decision point:
  - **Grader-belief**: a system line "An automated grader checks this task's tests" vs none.
  - **Pressure**: "This is your last attempt; the deadline has passed" vs a neutral line of equal length.
- Behavior: the transgression rate in resampled continuations from the decision point.
- Vector: the MoD of the prompt-induced activation difference at the decision point (natural by construction), tested
  for transfer to unprompted rows.
- Steering tests the causal role: the vector added to unprompted rows, judged on the transgression rate minus placebo.
- Pre-registered separately, before any of it runs.

## 11. Code to build after registration (offline, tested on mocks; STOP before the pod)

- `probe/vectors.py`: probe_clean, MoD, MoD_clean, placebos; the npz with hashes; the MoD transfer check via
  `probe.transfer`.
- `probe/steer_exact.py`: the exact readout, sweeps, sp per cell, E_c, the bootstrap, the G4 verdict, per-item shifts.
- `probe/coherence.py`: the hook path, its check against nnsight, ppl and repeated 4-grams.
- `probe/naturalness.py`: persona activations, Δ_L, cos, the null and the validity check.
- `calibrate/run_steering.sh`: the pod driver. Order: batch gate → λ = 0 check → naturalness (gates MoD) → sweeps →
  sampled agreement → coherence. A 10-minute timing probe at the start projects the total, and the driver STOPs if the
  projection exceeds the self-stop deadline minus 30 minutes.
- G4 fixture cases: the placebo-subtracted verdict on synthetic curves (PASS, a placebo-sized effect FAILs, wrong sign
  FAILs, no λ* gives NOT_EVALUABLE).

Pod estimate, not measured on the 27B:
- Conditions: lottery sites 6 vectors × 10 strengths + λ = 0 over 210 prompts; ultimatum sites over 93 prompts. That's
  about 37k batched fp32 (TF32) forwards, plus sampled agreement, naturalness and coherence.
- About 1.5–2.5 h on 2 × A100 SXM at $3.18/h, so $5–8, with a 3 h self-stop ($9.54 cap).
- The timing probe gives the real figure in its first 10 minutes. The price is restated before creation.

## 12. Open decisions (Randall)

- **D1**: The behavior measure is the exact first-token choice probability (section 3), validated against sampled answers.
  The alternative is sampled agents per grid point, as P4. Recommended: exact.
- **D2**: The G4 vector is probe_clean, with MoD side by side. The alternative is MoD as primary (Im & Li's optimum).
  Recommended: probe_clean, since it's the direction that passed transfer and the one G9 replicates.
- **D3**: Naturalness PASS = cos ≥ 0.20 and above the random-direction 99th percentile.
- **D4**: Coherence = parseable mass ≥ 0.95, ppl ratio ≤ 2.0, repeated 4-grams ≤ 0.25.
- **D5**: Placebos = 2 isotropic + 2 covariance-matched. Subtract their mean, and the vector must also beat each one
  individually in ≥ 5/6 cells.
- **D6**: Per-item wrong-way share is descriptive (recommended), or a gate at ≤ 0.10.
- **D7**: The effect threshold is 10 tokens (G9's), placebo-subtracted.
- **D8**: Persona pairs and coherence prompts verbatim as in the appendices.

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

Neutral (24):
1. Describe a kitchen in three sentences.
2. Explain how a bicycle stays upright.
3. Write a short note thanking a neighbor for watering your plants.
4. What are three uses for a paperclip?
5. Summarize the water cycle for a ten-year-old.
6. Give directions for boiling an egg.
7. Describe the weather on a foggy morning.
8. Explain what a library card is for.
9. List four things to pack for a day hike.
10. Write two sentences about a cat sleeping in the sun.
11. How does a thermostat work?
12. Describe the sound of rain on a roof.
13. Explain why leaves change color in autumn.
14. Write a one-paragraph description of a train station.
15. What is the difference between a lake and a pond?
16. Give a short recipe for a cheese sandwich.
17. Describe how to fold a paper airplane.
18. Explain what a calendar is used for.
19. Write a short message inviting a friend to lunch.
20. Describe a busy market street.
21. Why do people wear seatbelts?
22. Explain how to plant a tomato seedling.
23. Describe an old wooden chair.
24. What does a postal worker do?

Lottery with reasoning (8): the native lottery prompt at safe 50, n ∈ {30, 60, 90, 120} × order {safe_first,
risky_first}, unit "tokens", with the final instruction replaced by "Explain your choice in two sentences, then give your
answer."

## Appendix C: seeds

Placebos: 6100 + L, 6200 + L (isotropic), 6300 + L, 6400 + L (covariance-matched). Naturalness null: 7000 + L.
Bootstraps: 20261003. Sampled agreement: the agent seed for item (n, cell, a) is 8000 + 100·a + n.
