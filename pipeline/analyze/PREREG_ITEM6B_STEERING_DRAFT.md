# Item 6b: steering on Gemma-3-27B-IT, redesigned — pre-registration DRAFT

Status: **DRAFT, not registered; $0 until Randall's go.** It is built on Randall's 6b rulings of 2026-10-05 (PLAN_27B
decision 15). On approval it becomes gate rules 2026-10-06.1 (or the date of approval). The CAA vector is built on the pod
by frozen code, from frozen inputs, and hashed before any steered output (section 2.1). Every other vector is built and
frozen offline before the pod.

Inherited unchanged from item 6 (`PREREG_ITEM6_STEERING.md`), except where stated:
- the exact first-token readout: served primary, untruncated-softmax sensitivity, option ids == run 2's;
- the items (lottery, safe 50, 35 n × 6 cells = 210 prompts);
- the per-cell PAV switching points;
- the λ-0 check (logistic-lapse, as lambda0_checksum);
- the coherence rule (parseable mass ≥ 0.95, ppl ratio ≤ 2.0, repeated 4-grams ≤ 0.25);
- per-item shifts (descriptive);
- G4 as an instrument gate.

What changed, and why:
- the direction, the strength unit and the null;
- why: item 6 was NOT_EVALUABLE because the filter direction w/σ was off-manifold at every registered strength.

## 0. The post-hoc record (kept with this design)

The 6b step-(1) STOP fired under the raw-vs-isotropic magnitude rule. The rule was replaced after the result because its
null is miscalibrated: a covariance-matched placebo fails it too. The replacement (whitened cosine against the whitened
covariance-matched null) found the pattern a = Σw aligned with the within-level n slope and the frame-matched MoD
(`results/t4_27b_2026-10-05_item6b_offline/WHITENED_CHECK.md`), so the pattern is not an arm of 6b.

## 1. Arms (all at lottery L38; the primary site of item 6, a transfer-passing layer)

| arm | vector | role |
|---|---|---|
| **D: CAA answer-token contrast** | section 2.1 | **G4 instrument vector** |
| n-direction | the within-level n slope of the native training activations at L38 (all prompts; the stimulus direction, whitened cos 0.71 with the pattern) | comparison, descriptive |
| Fan z-space | unit logistic weight learned on z-scored features (Fan et al. 2026 as written), the G9 path's L38 | G9, descriptive; replication deviation recorded (section 7) |
| placebos | 16 covariance-matched (**primary null**) + 4 isotropic (secondary) | every arm's null |

Not in 6b: the pattern a = Σw (ineligible, section 0); the pattern with n partialled out (dropped); L30 and the ultimatum
sites (cost; they gate nothing).

## 2. Vectors

### 2.1 D: CAA answer-token contrast (built on the pod, frozen by hash before any steered output)
- **Contrast set:** every native prompt of the training levels (safe 30, 50, 100: 35 n × 6 cells × 3 = 630 prompts). Each
  is teacher-forced with BOTH native answers, "Safe Option" and "Risky Option", as the assistant turn.
- **Activation:** the block-38 residual (fp32) at the position of the first answer token ("Safe" / "Risky"; ids 39316 /
  99510).
- **v_raw** = mean over the 630 prompts of [x(Risky) − x(Safe)]. It is matched on the prompt by construction, so there
  is no between-prompt contrast.
- **Token-identity projection:** the answer-token embedding difference E[99510] − E[39316] and unembedding difference
  U[99510] − U[39316] (identical directions if the embeddings are tied; Gram-Schmidt either way) are projected out, then
  the vector is unit-normalized. → **v_CAA**
- **Freeze:** v_CAA is written to `caa_vector.npz` and its sha256 is logged to `checks.json` and the run log before the
  first steered forward. The build is deterministic (fp32 + TF32, fixed prompt order).
- **Descriptive, logged:** cos(v_CAA, probe_clean); cos with the homogeneous n directions, the n slope and the
  frame-matched MoD (raw and whitened, with the whitened null of `probe.whitened_check`); and the scale diagnostics of
  `probe.pattern_diag`.

### 2.2 n-direction and Fan vectors: built offline, frozen with sha256 before the pod
- n slope: X and n centered within level over the training split, least-squares slope, unit.
- Fan: the C chosen by CV (L38 site, C = 0.1); unit(w_z).

### 2.3 Placebos (new seeds; frozen offline)
- 16 covariance-matched: x ~ N(0, Σ_38) drawn as Xcᵀg, seeds 63000 + 100k + 38, k = 1..16.
- 4 isotropic: the frozen item-6 iso1–iso4 at L38 (61000 + 100k + 38).

## 3. Strength unit and grid (ruling: natural-projection SD per vector, placebos included)

- **Unit:** sd_v = the standard deviation of X @ unit(v) over the native training-split prompt-final activations at L38.
  This is computed offline for every vector except v_CAA, whose sd_v is computed on the pod from the same activation file
  and logged with its hash.
- **Strength:** k ∈ {−4, −2, −1, −0.5, −0.25, 0, +0.25, +0.5, +1, +2, +4} SD. The addition is k · sd_v · unit(v) at every
  position of block 38, the same injection path as item 6 with the scale in SD instead of the mean norm. **Every vector,
  placebos included, is scaled by its own sd_v.**
- **Expected direction:** +k toward Risky lowers the switching point (CAA, Fan). The n-direction arm's sign is fixed
  (+ = larger n); its expected effect is likewise a lower sp (a larger perceived stake).

## 4. Eligibility of strengths (defined before any steered output)

A symmetric strength ±k is **eligible** iff all three hold:
1. **the target is coherent** at +k and −k (item 6's coherence rule: parseable mass, ppl ratio, repeated 4-grams);
2. **the manipulation check holds for the target** at +k and −k: accuracy ≥ 0.95 (section 5);
3. **the null is intact**: at least 12 of the 16 covariance-matched placebos have parseable mass ≥ 0.95 at both +k and −k.

k* = the widest eligible k ≤ 2 SD. With no eligible k, G4 is NOT_EVALUABLE. The ±4 SD conditions are run and reported
(the dose curve), but they are never k*.

## 5. Behavioral manipulation check (ruling 2)

- **Prompts:** 36 items (safe 50; n ∈ {20, 50, 80, 110, 140, 170} × 6 cells). The native lottery prompt with the answer
  instruction replaced by "Before choosing, state the guaranteed amount and the chance of winning the risky option.
  Then stop."
- **Generation:** greedy, 40 new tokens, steered by the KV-cached decode loop.
- **Scoring (fixed parser in `probe/manipulation.py`):**
  - correct iff the first number following a "guaranteed"/"sure"/"safe" mention equals the safe amount, AND the reply
    states the probability as 50 % (forms: "50%", "50 percent", "0.5", "half", "1 in 2", "one in two");
  - accuracy = the share correct.
- **λ = 0 baseline:** accuracy ≥ 0.95 is required. Otherwise the check is NOT_EVALUABLE, and so is G4 (an instrument
  failure: the probe of comprehension does not work unsteered).
- **Runs on:** CAA and the n-direction arm at every strength; Fan at every strength (descriptive).
- **Also reported:** the stated guaranteed amount and probability under steering, so an arm that moves the perceived
  stake shows it.

## 6. G4 (CAA, L38, served readout): five criteria at k*, against the 16 covariance-matched placebos

E_c = [sp_c^CAA(+k*) − sp_c^CAA(−k*)] − mean over the 16 covariance-matched placebos of [sp_c^p(+k*) − sp_c^p(−k*)]

1. median over cells of E_c ≤ −10 tokens;
2. E_c < 0 in ≥ 5 of 6 cells;
3. the CAA symmetric effect exceeds each of the 16 covariance-matched placebos' in magnitude in ≥ 5 of 6 cells;
4. the grid-point cluster-bootstrap 95 % CI of the pooled E excludes 0 (2,000 draws, seed 20261003);
5. the pooled placebo-subtracted sp(k) is monotone in the expected direction over the eligible strengths (10 % dip
   tolerance).

- **Reported beside G4, gating nothing:** E against the 4 isotropic placebos; the softmax-readout verdict; per-item
  shifts.
- **Reading:** G4 is an instrument gate. A PASS shows that steering works in this pipeline, not that v_CAA is a
  risk-preference variable.

### 6.1 Option-relabeling readout control (descriptive, with a pre-registered reading)
- **Prompts:** the native lottery prompt with the options named "Option A" / "Option B" and the instruction "Answer with
  exactly one of: A, B." The first-listed option is "Option A", so the gamble is A in the risky_first cells and B in the
  safe_first cells (counterbalanced by the existing order cells).
  Readout: the exact first-token P(choosing the gamble) from the A/B token ids (resolved and checked at λ = 0).
- **Conditions:** v_CAA and 4 of the covariance-matched placebos (k = 1..4), every strength.
- **Reading, fixed now:** if G4 passes AND the relabeled placebo-subtracted effect at k* has the expected sign with its
  bootstrap CI excluding 0, the result reads "steers the choice". If G4 passes without that, it reads "steers the answer
  token".
- **Precondition:** the A/B first-token property, checked by λ-0 sampled agreement on the relabeled prompts (≥ 99 %).
  If it fails, the control is NOT_EVALUABLE; G4 is unaffected.

### 6.2 n-direction and Fan arms (descriptive)
- **Statistics:** the same statistics, eligibility and manipulation check, against the same 16 covariance-matched
  placebos.
- **Pre-registered comparison:** the n-direction arm shifting the stated stake (manipulation-check stake reports moving
  with k) while CAA does not would say CAA acts downstream of stimulus encoding.

## 7. G9 (Fan et al. replication): the deviations recorded

- **Layer:** G9 uses probe.train's layer, now restricted to transfer-passing layers (gate rules 2026-10-05.1): L38.
- **Vector:** Fan et al.'s z-space weight is reported in 6b as a descriptive arm in SD units.
- **Replication deviations, recorded:**
  1. Fan et al. do not state λ's units or range, or whether the addition repeats on generated tokens; 6b uses SD units
     at every position.
  2. Our G9 dial (probe.calibrate) steers the raw-space filter w/σ, not Fan's z-space weight.
  3. Fan steer the final-token representation; we add at every position.
- G9's own steered sweep keeps its registered rules and is not run in 6b.

## 8. Pod order and checks (STOP on any instrument failure)

1. **Instrument checks:** library pins (`replay.pins`); option ids; batch gate; HF-hook path check; λ-0 exact vs served.
2. **λ = 0 sampled agreement**, native prompts, through the KV-cached decode loop: the first generation on the pod
   (ruling 4).
3. **Relabeled controls at λ = 0:** relabeled sampled agreement (gates only the control), then the manipulation check at
   λ = 0 (gates G4).
4. **CAA:** build → project → sd → hash logged.
5. **Timing probe:** STOP if the projection exceeds the budget.
6. **Sweeps:** CAA → 16 covariance-matched → 4 isotropic → n → Fan.
7. **Steered checks:** coherence (targets every k; covariance-matched placebos 1–4 at every k, report-only); manipulation
   check (CAA, n, Fan); sampled agreement at ±1 SD for CAA (the first-token property under steering).
8. **Relabeled sweep:** CAA + 4 covariance-matched.
9. **Analysis, then DONE.**

## 9. Price

Measured on the item 6 pod: 0.048 s per exact-readout prompt; 12.2 s per coherence condition (32 prompts × 64 tokens + NLL).

| block | work | minutes |
|---|---|---|
| preflight + model load | | ~15 |
| instrument checks, λ-0 exact, λ-0 sampled agreement (native + relabeled), manipulation λ-0 | | ~9 |
| CAA build (630 prompts × 2 answers), projection, sd | 1,260 forwards | ~2 |
| sweeps: CAA, 16 cov, 4 iso, n, Fan | 23 vectors × 10 k × 210 = 48,300 forwards | ~39 |
| relabeled sweep: CAA + 4 cov, plus λ-0 | 10,710 forwards | ~9 |
| coherence: CAA, n, Fan (30) + cov placebos 1-4 (40) | 70 conditions | ~14 |
| manipulation check: CAA, n, Fan × 10 k | 30 conditions × 36 prompts × 40 tokens | ~4 |
| sampled agreement at ±1 SD (CAA) | 2,520 six-token answers | ~4 |
| analysis | | ~2 |
| **total** | | **~98 min ≈ 1.65 h** |

- **Expected:** ~1.65 h × $3.18/h ≈ **$5.25**, on 2× A100 SXM, EUR-IS-1.
- **Self-stop:** **2.5 h → cap $7.95**, inside the $8 cap, so all arms fit and the "D only" fallback isn't needed.
- **Budget STOP:** the timing probe stops the run if its projection exceeds the self-stop minus 30 minutes. If it STOPs
  for time, the fallback order is: drop Fan, then n, then the relabeled sweep. That leaves D + 16 covariance-matched
  + 4 isotropic (~25 min of sweeps).

## 10. Code to build after approval (offline; mock-tested; 4B smoke under the pins; STOP before the pod)

- `probe/caa.py`: teacher-forced answer-token residuals, the projection, sd, hash.
- `probe/manipulation.py`: prompts and the fixed parser, plus parser tests.
- Relabeled prompts and A/B readout in `probe/tasks.py` (a new regime, "relabeled").
- `probe/run_steering.py`: SD units (per-vector sd from the manifest; the CAA sd from the pod build); the eligibility
  rule of section 4; the arm list; the fallback order.
- `probe.vectors`: build the n, Fan and 16 covariance-matched vectors with sd_v, and freeze them.
- Tests: SD scaling, eligibility branches, the manipulation parser, the relabeling map, and the CAA projection on
  synthetic data.
- 4B smoke: CAA build, relabeled readout and the manipulation check on Gemma-3-4B with the pins.
