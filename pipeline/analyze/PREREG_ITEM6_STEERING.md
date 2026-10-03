# Item 6: steering on Gemma-3-27B-IT — design and pre-registration DRAFT

Status: **DRAFT v2, not yet registered.** D1–D8 were decided by Randall on 2026-10-03 (section 12). One blocker was found
while building, in the MoD definition (section 2.2), and registration as gate rules 2026-10-03.1 waits on Randall's
decision there. After registration the vectors are built, hashed and committed, all before any pod. No 27B steering data
exists. The code is built and mock-tested (section 11).

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

2.2 **MoD_clean: BLOCKER found while building (Randall to decide).** As drafted: within each stratum (level, n) where
both choices occur, mean(x | high) − mean(x | low), averaged over strata weighted by min(n_high, n_low), surface
directions projected out, unit-normalized.

The flaw: the prompt-final residual is a deterministic function of the prompt. Run 2 shows it: 280 prompts appear more
than once, and the activations within a repeated prompt are identical (max |ΔX| = 0.0). Each (level, n) stratum holds
exactly 6 distinct prompts, and they differ only by surface cell (order × unit). So a grid-point-matched difference of
means can only contrast surface cells: the framings that tilt toward risky at that n against those that tilt toward
safe. It is a framing direction by construction. Cleaning removes the linear order and unit effects, which leaves
their interaction.

The diagnostics in `probe.vectors`, computed on the trial build before this was seen:

| site | cos(MoD_clean, probe_clean) | MoD transfer (2026-10-02.1) |
|---|---|---|
| lottery L38 | 0.175 | 0.860 PASS |
| lottery L30 | 0.197 | 0.676 FAIL |
| ultimatum L40 | 0.065 | 0.722 PASS |
| ultimatum L46 | 0.077 | 0.491 FAIL |

The unmatched class-mean difference is mostly the number in the prompt: cos with the n direction is 0.96 / 0.85 / 0.75 /
0.79. A transfer PASS for a framing direction is what PLAN_27B decision 13 warns about.

Options:
- **(A, recommended) Match on the frame, not the grid point.** Strata = (level, order, unit): within a fixed frame and
  safe amount, mean(x | high) − mean(x | low) across n. This is the contrast between choice classes that holds
  everything but the stimulus magnitude fixed, which is the only thing the choice responds to inside a frame. Expect it
  to sit close to the probe direction and to the within-level n direction; both cosines would be reported.
- **(B) MoD from the persona contrast pairs** (same item, risk-seeking vs risk-averse line). This is Im & Li's
  contrast-pair form, but the naturalness check (cosine with the same persona shift) becomes circular. It would need a
  held-out persona split: two pairs build it, two judge it.
- **(C) Drop MoD from item 6** and report the defect.

Recommendation: A. Decide on the construct, before any number for A or B is computed. No transfer AUROC or cosine has
been computed for A or B.

2.3 **Placebos** (D5):
- **Primary site (lottery L38): 16 unit vectors**, 8 isotropic (uniform on the sphere) and 8 covariance-matched
  (x ~ N(0, Σ_L), Σ_L from the centered training-split activations at L, drawn as Xcᵀg without forming Σ).
- **Every other site: 4 vectors** (k = 1, 2 of each kind).
- **Seeds:** isotropic k = 61000 + 100k + L; covariance-matched k = 62000 + 100k + L (k = 1..8). All 64 are distinct;
  checked in the tests.
- **Norm matching** is automatic: every vector is unit-normalized and scaled by the same λ × mean residual norm.

2.4 **Freeze**: all vectors go to `steering_vectors.npz` with their sha256 in the registration commit, before the pod.

### 2.5 Use gates for MoD vectors (before any steering with them)

a. **Transfer** (gate rules 2026-10-02.1, unchanged): the MoD_clean direction's agent-regime AUROC ≥ 0.70 with the 95 %
   cluster-bootstrap lower bound > 0.50. Offline on run 2's agent activations, $0, before the pod.

b. **Naturalness**: does the vector point where a prompt that induces the behavior moves the residual? Four persona pairs
   per task (Appendix A, fixed here), each a one-sentence line prepended to the native user turn. The prompt-induced
   shift Δ_L = mean over the reference-level items and the four pairs of h(high-persona) − h(low-persona) at the
   prompt-final position of layer L. Naturalness = cos(v, Δ_L), 95 % CI by bootstrap over items × pairs.
   - 95 % percentile CI by bootstrap over (item, pair) units. It sits below the point estimate when Δ_L is noisy, because a
     resampled mean adds noise and noise shrinks a cosine. The decision uses the point estimate.
   - Interpretation (D8): lottery personas 1, 3 and 4 share vocabulary with the option text ("risks", "gambles", "sure
     thing", "guaranteed amount"). So Δ_L includes lexical priming of the option words, not only an induced disposition.
   - Validity: the personas must move the behavior. The exact P(high) (section 3) must be higher under the
     high persona, averaged over items, with a CI excluding 0. Otherwise the check is NOT_EVALUABLE: the prompt doesn't
     induce the behavior.
   - PASS (**decision D3**): cos(v, Δ_L) ≥ 0.20, and above the 99th percentile of |cos(Δ_L, r)| over 1,000
     covariance-matched random directions r.
   - Computed on the pod before the sweeps. A MoD vector that fails (a) or (b) isn't steered; its numbers are reported.
   - The probe vectors get the same naturalness number, reported but not gating, since their use gate is transfer,
     which they passed.

## 3. Behavior readout: exact first-token choice probability (D1, approved)

In all 1,368 native trials of run 2 the first token alone decides the choice: each task has exactly two first tokens,
one per option (lottery: `Safe` 606 → low, `Risky` 514 → high; ultimatum: `Accept` 228 → high, `Reject` 20 → low).
So under steering, the behavior is read exactly from one forward per prompt:

  **served (primary)**: q = the first-token sampling distribution at T = 0.8, top-p 0.95 (the probe track's settings;
  nucleus as vLLM and this repo's sampler: keep a token while the cumulative mass before it is < 0.95)
  **softmax (sensitivity, D1)**: q = the untruncated softmax at T = 0.8, reported beside every effect. Top-p can drop an
  option from the nucleus and put a step in the dose curve that the model's preferences don't have.
  P_i(λ) = q(high tokens) / q(high ∪ low tokens);  m_i(λ) = q(high ∪ low tokens), the parseable mass
  Option tokens: the first token of each option name, which must equal run 2's observed ids (lottery 99510 / 39316,
  ultimatum 24040 / 137256); the pod STOPs otherwise.

Items: lottery at safe 50: 35 values of n × 6 surface cells (order × unit) = 210 distinct prompts. Run 2's 280
reference trials are 8 seeds over these 210 prompts. Ultimatum: 31 offers × 3 units = 93 prompts.

Why replace sampled agents:
- Sampling noise was the binding constraint on the 9B (2 agents per cell per grid point → NOT_EVALUABLE).
- Placebos at every strength become affordable.
- Per-item shifts become exact, not estimates.

Instrument checks on the pod (STOP on failure):
- **λ = 0**, per task: the same estimator as `probe.calibrate.lambda0_checksum` (the lapse-aware logistic,
  `psychometric.switching_point`). Served side: run 2's reference-level labels (lottery 88.5). Exact side: the λ-0
  probabilities as soft labels (`switching_point_soft`), weighted to run 2's (n, cell) trial counts. Pass if |gap| ≤ 2 ×
  the served sp's within-grid-point bootstrap SE; the exact side has no sampling noise. The PAV crossings of both are
  reported beside it. Run 2's pooled served curve plateaus at 0.375 from n = 55 to 110, a mixture of surface cells, so
  its PAV crossing (111 ± 12) and its logistic-lapse estimate (88.5 ± 4) differ. The check uses the drafted rule's
  estimator.
- **Sampled agreement**: probe_clean at L38, λ ∈ {−0.4, 0, +0.4}, 6 sampled agents per item (T 0.8, top-p 0.95, 6 new
  tokens; seed 1,000,000 + 10,000·cell + 1,000·agent + n). Two conditions at each λ:
  1. The first token decides the parsed answer in ≥ 99 % of parseable answers. This is the property the exact readout
     rests on, checked under steering.
  2. Per cell, the sampled switching point (PAV) lies within 2 SE of the exact one (SE by within-grid-point bootstrap,
     floor 1 token; both saturated counts as agreement) in at least 5 of 6 cells.
- **Path check**: the HF-hook path used for generation equals the nnsight path's prefill log-probs (top-20, within the
  batch gate's tolerance) at λ ∈ {0, ±0.4}, and the GPU readout equals the numpy readout to 1e-6.
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
   - Required for the steered vectors at every λ. For placebos, it runs at the primary site on 4 of the 16 (iso1, iso2,
     cov1, cov2) and is reported only; placebo coherence gates nothing.

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
3. In at least 5 of 6 cells, v's own symmetric effect exceeds **all 16** placebos' individually in magnitude (D5). The
   pooled E subtracts the 16-placebo mean.
4. The 95 % CI of the pooled E (the mean over cells) excludes 0. Bootstrap over grid points n as clusters (all 6 cells
   of an n together), 2,000 draws.
5. Dose curve: the pooled placebo-subtracted sp(λ) is monotone in the expected direction over the coherent strengths
   (G4's `coherent()`, 10 % dip tolerance).

Secondary and exploratory (MoD at L38; both vectors at L30; the ultimatum sites) get the same statistics against their
own placebos. They are reported side by side, MoD next to the probe direction, and gate nothing. The ultimatum has 3
cells (unit only; its prompt has no option order), so its "n − 1 of n" reads 2 of 3. D2: if MoD steers clearly better
than probe_clean, that is reported as a finding; it does not change G4.

## 7. Per-item shifts (reported for every vector, at λ* and at every coherent strength)

d_i(±λ) = [P_i^v(±λ) − P_i^v(0)] − mean over placebos p of [P_i^p(±λ) − P_i^p(0)]

Reported overall and per cell:
- quantiles 5/25/50/75/95;
- share moved, |d_i| ≥ 0.05;
- share moved the wrong way: the sign opposite to the expected one and |d_i| ≥ 0.05;
- the mean d_i on items already at the target side at λ = 0 (P_i(0) ≥ 0.9 for +λ, ≤ 0.1 for −λ), the ceiling and
  floor items.

Descriptive (D6).

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

## 11. Code (built 2026-10-03, offline; nothing has run on the 27B)

| file | what |
|---|---|
| `probe/vectors.py` | probe_clean (the `probe.transfer` construction, so its transfer verdict applies to this exact vector), MoD (definition pending, 2.2), placebos, `steering_vectors.npz` + `vectors_manifest.json` with per-vector and file sha256, the MoD transfer gate; `--verify` |
| `probe/steer_exact.py` | served and softmax readouts, PAV switching points per cell, λ*, E_c, the grid-point cluster bootstrap, the G4 verdict, per-item shifts |
| `probe/psychometric.py` | + `switching_point_soft` (the lapse-aware logistic on probabilities; the λ-0 check) |
| `probe/coherence.py` | the 32 prompts verbatim, repeated 4-grams, the ppl ratio, the coherence rule |
| `probe/naturalness.py` | the persona pairs verbatim, Δ_L, cos, the covariance-matched null, validity, the verdict |
| `probe/steer_backend.py` | MockBackend (offline) and TorchBackend (pod: nnsight readout and residuals; HF forward hook + KV-cached generate for coherence and sampled agreement; per-row seeded sampling through a logits processor) |
| `probe/run_steering.py` | the driver: frozen-vector check → option ids → batch gate + path check → λ-0 per task → timing probe (STOP if the projection exceeds the budget) → naturalness → per site (primary first): sweeps, sampled agreement, coherence → analysis. Resumable (each finished condition is a logged line) |
| `probe/steer_analyze.py` | steering.json and STEERING.md, offline-rerunnable after copy-back |
| `calibrate/run_steering.sh` | the pod wrapper (self-stop registration, weight preflight, fp32 + TF32) |
| `probe/test_steering.py` | 28 tests: the readout's top-p step, sp, every G4 branch (PASS; placebo-sized FAIL; wrong sign; one placebo bigger; saturation NOT_EVALUABLE; λ* fallback; instrument NOT_EVALUABLE), per-item, coherence, naturalness, seeds, covariance placebo, freeze tamper, and the mock driver end to end (budget STOP, PASS, resume, unfrozen-vector STOP) |
| `probe/smoke_steer_4b.py` | TorchBackend on Gemma-3-4B-IT locally (same class and tokenizer): readout, path check, generation, seeded sampling, the first-token property, NLL, residual capture, batch gate |

Pod estimate (not measured on the 27B), counting MoD at every site as the upper bound:
- 61,560 exact-readout prompt forwards:
  - lottery L38: 18 vectors × 10 strengths × 210 prompts;
  - lottery L30: 6 × 10 × 210;
  - ultimatum: 2 sites × 6 × 10 × 93.
- 2,424 naturalness forwards, 3,780 six-token sampled answers, and 120 coherence conditions (32 prompts × 64 tokens,
  plus NLL).
- At an assumed 0.05–0.10 s per batched fp32 prompt forward that's ~1.5–2.5 h, plus ~15 min load and preflight. So
  **~2–3 h on 2 × A100 SXM at $3.18/h, ~$6–9.5; a 3.5 h self-stop caps it at $11.13.**
- The driver's timing probe measures the real rate on the first condition and STOPs if the projection exceeds the
  budget passed in (the self-stop deadline minus 30 minutes).

## 12. Decisions (Randall, 2026-10-03)

- **D1 approved**, plus a sensitivity readout. The primary readout is the served first-token distribution (T = 0.8, top-p
  0.95). The pre-registered sensitivity readout is the untruncated softmax at T = 0.8, reported beside every effect.
- **D2 approved.** probe_clean decides G4, with MoD side by side. If MoD steers clearly better, that is reported as a
  finding.
- **D3 approved.** cos ≥ 0.20 binds; the random 99th percentile is ~0.035 at this d_model.
- **D4 approved.**
- **D5 changed.** The primary site gets 16 placebos (8 isotropic + 8 covariance-matched). Criterion 3: v beats all 16 in
  ≥ 5/6 cells. The pooled E subtracts the 16-placebo mean. The other sites keep 4. Pod time re-estimated (section 11).
- **D6: descriptive.**
- **D7 approved** (10 tokens).
- **D8 approved verbatim**, with the lexical-priming note in the interpretation (section 2.5b).
- **Pending: the MoD definition (section 2.2).** Registration waits on it.

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
risky_first}, unit "tokens". The sentence "Answer with exactly one of: Safe Option, Risky Option." is replaced by
"Explain your choice in two sentences, then give your answer."; the question "Which option do you choose?" stays.

## Appendix C: seeds

Placebos: isotropic k = 61000 + 100k + L, covariance-matched k = 62000 + 100k + L (k = 1..8 at lottery L38, 1..2
elsewhere). Naturalness null: 70000 + L. Bootstraps: 20261003. Sampled agreement: seed 1,000,000 + 10,000·cell + 1,000·agent
+ n (per-row seeded; the draft's 8000 + 100·a + n collided across cells and agents).
