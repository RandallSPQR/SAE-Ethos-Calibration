# Gate rules changelog

## 2026-09-16.3 (probe track protocol; G0-G9 criteria unchanged)

- **Lottery is two-dimensional**: safe amount in {30, 50, 70, 100} x risky reward 10..180. The decision
  variable the probe must find is the gamble's attractiveness relative to the sure thing, not the value
  of n. Safe = 50 is the reference (Fan et al.'s condition) for baselines and calibration.
- **Held-out level**: an entire safe level (70) is held out of probe training; `heldout_acc` is measured
  there. `per_layer` reports held-out accuracy at every candidate layer at the chosen C.
- **Surface variation**: option order (safe-first / risky-first) and unit word (tokens / points /
  dollars) cycle across agents; the numeric fallback in the parser maps through the trial's order.
- **Sampling**: T = 0.8 across agents (P1 and the steered sweeps), temperature recorded per trial.
- **Lambda sweep**: log-spaced from +-0.01 to +-0.4 (was G4's +-0.6 linear); calibration finds where
  the step starts to move.
- Rationale: at T=0.8 the residual at the prompt-final position is still a deterministic function of the
  prompt, so temperature alone would have left the probe fitting the literal number with label noise on
  top; only prompt variation decorrelates the direction from the digit. Run 1's +-0.6 sweep saturated at
  every non-zero lambda.

Every change to WHAT a gate measures gets an entry here and a bump of `gates/_common.GATE_RULES_VERSION`.
The version is written into every gate result, every T1 report JSON, and the provenance manifest, so a
PASS is always relative to a named ruleset. "PASS after fixing the statistic" without an entry here reads
as "tuned"; with one it reads as "corrected, and here is why".

## 2026-09-16.2 (amended after run 2, same version: no gate outcome on run-2 artifacts changes except G2's decoy term)

- **Probe track amendment (P1/P3)** — Trials are sampled at `probe.temperature` (0.8) instead of T=0; the
  temperature is recorded per trial. P1 STOPs on a saturated or hard-step unsteered curve; P3 refuses a
  single-class label set or a label that is a step function of the parameter. Ultimatum grid starts at 0.
  Rationale (run 1 on the pod): at T=0 Gemma-2-9B-IT is a step (Safe <= 50 / Risky >= 55: it switches
  when the jackpot exceeds the sure amount, ignoring the coin flip; Fan's Llama: 125) and accepts every
  ultimatum offer from 10 up; the seed axis is degenerate at T=0 (2 distinct responses in 280 trials), so
  "held-out 1.0" measured the prompt's number, not a trait, and steering could only leave the step or
  flip the whole grid. G9 itself is unchanged.
- **G4 amendment** — `coherent()` tolerates single-step reversals up to 10% of the total effect. The
  fp32 run 2 curve (0.057, 0.092, 0.121, 0.131, 0.128, 0.135, 0.172) dipped 0.003 at one step of a
  0.116 effect; the strict rule called that non-monotone. A reversal larger than the tolerance still fails.
- **G2 amendment** — Scaled copies of the chosen tensor (x0.8, x1.2) are excluded from the decoy-margin
  rule and reported as `ve_scale_sensitivity`. Run 2 showed the SAE reconstructs the x1.2 copy slightly
  BETTER than the unscaled tensor (VE 0.768 vs 0.757), i.e. VE cannot detect a scale error; L0 can
  (59 / 86 / 118) and tensor identity is the arbiter. Artifact identity now resolves repo/path through
  SAELens's pretrained directory (the cfg no longer carries them).

## 2026-09-16.2

Triggered by the first full GPU run (A100, artifacts in `pipeline/results/t1_2026-09-16/`).

- **G1** — Mode (exact vs logprob) now comes from the transcript's own `sampling.temperature`, never from
  config; a transcript without the field fails. Exact mode gains a CONDITIONAL excuse: an argmax flip is
  excused only where generation's top-2 margin was < `g1_flip_margin_excuse` (0.25 nats) AND the sampled
  token is in replay's top-2. A wide-margin flip is still a hard fail. Rationale: one near-tie flip
  (0.13 nats apart, max logprob gap 0.17) is kernel-order noise between vLLM and transformers under bf16;
  a template mismatch produces many flips clustered at the turn boundary. The fp32 stage
  (`run_t1.sh --fp32`) exists to prove the flip is numerical, not to replace the rule.
- **G2** — Hook identification is VARIANCE EXPLAINED with a required absolute margin over every decoy
  (`g2_decoy_ve_margin_min` 0.10). A published-L0 match is reported, not gated: L0 cannot separate
  resid_post(31) from resid_pre(31) = resid_post(30) by construction. New optional checks that BLOCK when
  present and failing: tensor identity against TransformerLens `blocks.31.hook_resid_post` loaded with
  no weight processing — RELATIONAL tolerance, per-position cosine >= `g2_identity_min_cos` (0.999) and
  relative norm difference <= `g2_identity_max_norm_rel` (1e-2), run in fp32 because element-wise bf16
  atol fails a correct hook on 31 layers of kernel-order noise — and JumpReLU encode integrity (fraction of active
  features below their own threshold must be 0). Scaled copies of the chosen tensor (x0.8, x1.2) join
  the decoy set. L0 is measured per document (own BOS, 1024 ctx, BOS excluded) and the distribution is
  reported. Rationale: 0.73 VE beat every decoy but is below what the release implies; beating decoys
  shows "best offered", identity shows "right".
- **G3** — Discrimination scored on 16-token WINDOW-MAX activations plus fraction-active, not per-position
  AUROC. Rationale: a labeled SAE feature is sparse; feature 8209 fired on 16% of code tokens and 0% of
  prose/prose-about-code (perfect specificity) yet scored 0.578 per position because of ties at zero.
  Window-max: 1.000. Fixture now includes the sparse case.
- **G4** — Readout must sit at a live decision point; the report records the prompt and greedy samples
  per strength. Rationale: after "Write something." P(code opener) is ~1e-7 and no absolute effect can
  appear; at a coding request it is 0.13 and moves 0.055 -> 0.173 monotonically (effect 0.118).
- **G5** — Reframed from abstention to DISCRIMINATION: paired real/null verbalizations, pass iff the real
  description matches its text's label and the matched-null one does not, at rate >=
  `g5_paired_discrimination_min` (0.60). Confabulation rate is reported and sets the oracle's evidential
  weight ("paired only" above `g5_confab_rate_max`). Rationale: the Karvonen oracle has no abstain mode
  (11/12 correct on real activations, 100% confident on null); that is a property of the artifact.

## 2026-09-10.1

Initial rules as scaffolded (per-position AUROC for G3, static readout for G4, abstention-based G5,
FVU+L0 match for G2 relaxed to "whichever is published").
