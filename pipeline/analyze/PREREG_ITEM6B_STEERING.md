# Item 6b: steering on Gemma-3-27B-IT, redesigned — pre-registration (gate rules 2026-10-06.1)

**Registered 2026-10-06, before any 6b output** (Randall: 6b rulings of 2026-10-05, PLAN_27B decision 15; draft approved
with amendments 1–5 on 2026-10-06).
- **Offline vectors:** frozen with sha256 in `probe/STEERING6B_FREEZE.json` before the pod.
- **The two CAA vectors:** built on the pod by the frozen code in `probe/caa.py`, from frozen inputs; each hash is logged
  before any steered output.
- **Pod cap:** 2.5 h / $7.95.

**The sole confirmatory test is G4: D at lottery L38 at k\*** (amendment 5). Everything else in this document is
descriptive.

Inherited from item 6 (`PREREG_ITEM6_STEERING.md`), except where stated below:
- the served first-token readout (primary) and the untruncated softmax (sensitivity);
- the per-cell PAV switching points;
- the λ-0 estimator (logistic-lapse, as lambda0_checksum);
- the coherence rule (parseable mass ≥ 0.95, ppl ratio ≤ 2.0, repeated 4-grams ≤ 0.25);
- per-item shifts;
- G4 as an instrument gate.

## 0. The post-hoc record

The 6b step-(1) STOP fired under the raw-vs-isotropic magnitude rule. That rule was replaced after the result because its
null is miscalibrated: a covariance-matched placebo fails it too. Under the replacement (whitened cosine against the
whitened covariance-matched null), the pattern a = Σw aligns with the within-level n slope and the frame-matched MoD, so
the pattern is not an arm of 6b (`results/t4_27b_2026-10-05_item6b_offline/`).

## 1. Prompt sets and disjointness (amendment 4)

- **Training set (vector construction only):** the native lottery items of the probe's training levels, safe 30 / 50 /
  100: 35 n × 6 cells (order × unit) × 3 levels = **630 prompts**.
- **Evaluation set (every steered readout, check and generation):** items at the **held-out safe level 70**:
  - the G4 readout: 35 n × 6 cells = 210 prompts;
  - the relabeled readout: the same 210 items, A/B format;
  - the manipulation check: 108 prompts;
  - the 8 lottery coherence prompts.
- **Overlap:** an item key is (level, n, unit, order). The overlap between the training keys and every evaluation set is
  computed on the pod and logged; it must be 0, or the run STOPs. All evaluation keys are at level 70, and no training
  key is.
- **Neutral prompts:** the 24 neutral coherence prompts are not lottery items.

## 2. Formats (verbatim)

- **Native:** `probe.tasks.messages(task, n, level, cond)` as in every earlier run. Answers "Safe Option" / "Risky
  Option"; first-token ids 39316 / 99510.
- **Relabeled A/B:**
  - The native prompt with the option names replaced by letters and the answer instruction changed:
    ```
    SAFE VS. RISKY CHOICE GAME

    You must choose between two options:
    Option {X}: Guaranteed {safe} {unit}.          <- the cell's order decides which line comes first
    Option {Y}: 50% chance of {n} {unit}, 50% chance of 0 {unit}.

    Which option do you choose? Answer with exactly one of: A, B.
    ```
  - {X} is the safe option's letter. Letters are assigned per prompt so that exactly half of each (level, unit, order)
    stratum has Safe = A, by a seeded permutation: seed 64000 for the training set, 64100 for the evaluation set.
  - Letter position, letter identity and option are therefore counterbalanced independently.
  - Answers "A" / "B". Their first-token ids are resolved from the tokenizer and logged; the first-token property is
    checked by λ-0 sampled agreement.

## 3. Vectors

### 3.1 D: counterbalanced A/B CAA (amendment 1; the G4 vector; built on the pod)
For each of the 630 training prompts in the A/B format (training letters), teacher-force both answers ("A" and "B") as
the assistant turn. Read the block-38 residual (fp32) at the answer token.

D_raw = mean over prompts of [x(answer = the risky option's letter) − x(answer = the safe option's letter)]

Then:
- project out the answer tokens' embedding and unembedding difference (E[A] − E[B] and U[A] − U[B]; Gram-Schmidt);
- unit-normalize.

Because Safe = A on exactly half of the prompts, the letter-identity component cancels in the mean; the projection removes
any residue.

### 3.2 D_word: the Safe/Risky-word CAA (the relabeling cross-check vector, descriptive)
- **Construction:** the same, on the 630 training prompts in the native format, with answers "Safe Option" / "Risky
  Option" and activations at their first token.
- **Projection:** E/U[99510] − E/U[39316].

### 3.3 Offline vectors (frozen before the pod; `results/t4_27b_2026-10-06_item6b_vectors/`)
- **n-direction:** the least-squares slope of X on n with both centered within level, over the native training split at
  L38; unit.
- **Fan z-space:** the unit logistic weight learned on z-scored training activations at L38 (C = 0.1, the item-6 CV
  choice), as Fan et al. 2026 write it.
- **16 covariance-matched placebos (primary null):** x ~ N(0, Σ_38) drawn as Xcᵀg; seeds 63000 + 100k + 38, k = 1..16.
- **4 isotropic placebos (secondary):** the frozen item-6 iso1–iso4 at L38.

## 4. Strength unit and grid

- **Unit:** sd_v = the standard deviation of X @ unit(v) over the native training-split prompt-final activations at L38
  (840 trials over the 630 prompts). For the two CAA vectors it is computed on the pod from the same activation file
  (run 2, on the volume) and logged with their hashes.
- **Strength:** k ∈ {−4, −2, −1, −0.5, −0.25, 0, +0.25, +0.5, +1, +2, +4} SD. The addition is k · sd_v · unit(v), added
  at every position of block 38 as an **absolute** displacement (`replay.hooks`, absolute mode). Every vector, placebos
  included, uses its own sd_v.
- **Expected direction:** +k (toward the risky choice) lowers the switching point.

## 5. On-manifold STOP for D (amendment 3)

Right after building D, on the pod, log D's scale diagnostics (`probe.pattern_diag.scale` on the training-split
activations):
- the worst-dimension push of a 1-SD step;
- the low-variance share;
- cosines with probe_clean, the homogeneous n directions, the n slope and the frame-matched MoD, raw and whitened.

**STOP before the sweep** if D's worst-dimension push of a 1-SD step exceeds the isotropic placebo range at L38. The
threshold, computed offline on 2026-10-06 from iso1–iso4 with training-split SDs, is 0.67–2.93, so **STOP if > 2.93**.
G4 is then NOT_EVALUABLE.

## 6. Eligible strengths (fixed now, before any steered output)

A symmetric strength ±k is **eligible** iff all three hold:
1. D is coherent at +k and −k (the coherence rule);
2. **the manipulation check passes for D at +k and −k** (section 7);
3. at least 12 of the 16 covariance-matched placebos have served parseable mass ≥ 0.95 at both +k and −k.

k* = the widest eligible k ≤ 2. Without an eligible k, G4 is NOT_EVALUABLE. The ±4 conditions are reported (the dose
curve) and are never k*.

## 7. Manipulation check (amendment 2)

- **Prompts (N = 108):** safe 70; n ∈ {10, 20, ..., 180} (18 values) × 6 cells. In the native prompt, "Answer with exactly
  one of: Safe Option, Risky Option." is replaced by "Before choosing, state the guaranteed amount and the chance of
  winning the risky option. Then stop."
- **Generation:** greedy, 48 new tokens, the KV-cached decode loop with the steering hook.
- **Parser (`probe/manipulation.py`, fixed):** correct iff BOTH hold:
  - **amount:** the first integer within 60 characters after a match of `guarantee|sure|safe` (case-insensitive) equals 70;
  - **probability:** the text matches `50 ?%|50 percent|fifty percent|0\.5\b|\bhalf\b|1 in 2|one in two|1/2`
    (case-insensitive).
- **Pass at a strength:** the one-sided 95 % lower bound of acc(k) − acc(0) is ≥ −0.05. The bound is the 5th percentile
  of 2,000 paired bootstrap draws over the 108 items, seed 20261006.
- **Instrument floor:** acc(0) ≥ 0.80. Below it the check cannot detect a loss, so it is NOT_EVALUABLE and so is G4.
- **Runs on:** D at every k (it gates eligibility); n and Fan at every k (descriptive). The stated amounts and
  probabilities under steering are reported.

## 8. G4 (the confirmatory test)

D at L38, served readout, on the 210 evaluation prompts, at k*, against the 16 covariance-matched placebos:

E_c = [sp_c^D(+k*) − sp_c^D(−k*)] − mean over the 16 placebos of [sp_c^p(+k*) − sp_c^p(−k*)]

**PASS** iff all five hold:
1. median over cells of E_c ≤ −10 tokens;
2. E_c < 0 in ≥ 5 of 6 cells;
3. |D's symmetric effect| exceeds each of the 16 placebos' in ≥ 5 of 6 cells;
4. the grid-point cluster-bootstrap 95 % CI of the pooled E excludes 0 (2,000 draws, seed 20261003);
5. the pooled placebo-subtracted sp(k) is monotone in the expected direction over the eligible strengths (10 % dip
   tolerance).

**NOT_EVALUABLE** if an instrument check fails (section 10) or no k* exists; FAIL otherwise. G4 is an instrument gate: a
PASS shows that steering works in this pipeline, not that D is a risk-preference variable.

## 9. Descriptive analyses (amendment 5: none of these is a test)

- **Relabeling cross-check:**
  - D_word steered on the A/B evaluation prompts (evaluation letters), read out as P(choosing the risky option's
    letter), against covariance-matched placebos 1–4 (each in its own SD units).
  - **"Holds" (amendment 2)** iff its placebo-subtracted pooled effect at k* has a 95 % CI excluding 0 AND is ≥ 0.5 ×
    G4's pooled E, same sign.
  - **Reading, fixed now:** G4 PASS + holds → "steers the choice across answer formats". Otherwise the format
    dependence is reported.
  - **Precondition:** the A/B first-token property (λ-0 relabeled sampled agreement ≥ 99 %); without it the cross-check
    is NOT_EVALUABLE.
- **n-direction and Fan arms:** the same statistics, eligibility and manipulation check, against the same 16 placebos.
  If the n arm moves the stated stake (the manipulation-check amounts drift with k) while D does not, that reads as D
  acting downstream of stimulus encoding.
- **Also reported:** E against the 4 isotropic placebos; the softmax verdict; per-item shifts; D's and D_word's
  diagnostics; cos(D, D_word); naturalness is not run in 6b.

## 10. Pod order (STOP on any instrument failure)

1. **Before any model work:** pins; option ids (native, plus A/B resolved and logged); the overlap log (must be 0).
2. **Instrument checks:**
   - batch gate;
   - HF-hook path check, in relative AND absolute modes;
   - λ-0 exact vs served at level 70 (run 2's served level-70 trials; logistic-lapse; within 2 SE);
   - λ-0 sampled agreement through the KV-cached decode loop: native evaluation items (gates G4), then relabeled
     evaluation items (gates the cross-check only);
   - manipulation check at λ = 0 (floor 0.80).
3. **CAA build:** D and D_word (sd, hash, diagnostics), then the on-manifold STOP (section 5).
4. **Timing probe:** STOP if the projection exceeds the self-stop minus 30 minutes. The fallback order on time: drop
   Fan, then n, then the relabeled cross-check.
5. **Sweeps:** D, the 16 covariance-matched, the 4 isotropic, n, Fan.
6. **Steered checks:** coherence (D, n, Fan at every k; covariance-matched 1–4 report-only); manipulation (D, n, Fan at
   every k); sampled agreement for D at ±1 SD (first-token property under steering; descriptive).
7. **Relabeled cross-check sweep.**
8. **Analysis, then DONE.**

## 11. G9 deviations (recorded; G9 itself not run in 6b)

- G9's layer is restricted to transfer-passing layers (gate rules 2026-10-05.1): L38.
- Replication deviations from Fan et al.:
  1. their λ units and range are unstated, and whether the addition repeats on generated tokens is unstated (6b uses
     SD units, every position);
  2. our G9 dial steers the raw-space filter w/σ, not their z-space weight;
  3. they steer the final-token representation.

## 12. Price

| block | minutes |
|---|---|
| preflight + model load | ~15 |
| instrument checks: λ-0 exact, λ-0 agreement (native + relabeled), manipulation λ-0 (108 prompts), overlap | ~10 |
| CAA builds: D + D_word, 2 × 630 × 2 forwards, plus diagnostics | ~3 |
| sweeps: 23 vectors × 10 k × 210 = 48,300 readouts at 0.048 s | ~39 |
| relabeled cross-check: (D_word + 4 cov) × 10 × 210 + λ-0 | ~9 |
| coherence: D, n, Fan (30) + cov1–4 (40) at 12.2 s | ~14 |
| manipulation: (D, n, Fan) × 10 k × 108 prompts × 48 tokens | ~12 |
| sampled agreement for D at ±1 SD | ~4 |
| analysis | ~2 |
| **total** | **~108 min ≈ 1.8 h** |

2 × A100 SXM at $3.18/h: expected **≈ $5.70**, with a self-stop of **2.5 h → $7.95** (the cap).
