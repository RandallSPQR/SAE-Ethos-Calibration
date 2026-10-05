# Item 6b, offline work (Randall 2026-10-05; $0; no pod). STOPPED at (1): the pattern vector fails the magnitude rule as stated

Item 6 is recorded as NOT_EVALUABLE (an instrument failure; the w/sd diagnosis is post-hoc and descriptive). This directory
holds the 6b offline diagnostics. Full tables: `DIAGNOSTICS.md` / `diagnostics.json` (`python -m probe.pattern_diag`).

## (1) Pattern vectors a = S w (Haufe), per site
- **Scale: on-manifold, as intended.** A 1-sd step along the pattern moves no dimension more than 1.1-1.2 of its own
  sds (probe_clean: 33-107; isotropic placebo 0.3-2.9; covariance-matched placebo 1.6-2.2). Low-variance share 0.004-0.006.
- **Magnitude rule: STOP fires.** |cos(pattern, n direction from choice-homogeneous prompts)| vs the isotropic magnitude
  null (99th pct ~0.035):

| site | below: pattern / iso null / cov null | above: pattern / iso null / cov null | all-prompt n slope | MoD frame-matched | MoD grid-matched (clean) | probe_clean |
|---|---|---|---|---|---|---|
| lottery L38 | 0.658 / 0.035 / 0.806 | 0.037 / 0.037 / 0.458 | 0.968 | 0.984 | 0.375 | 0.127 |
| lottery L30 | 0.300 / 0.036 / 0.506 | -0.201 / 0.036 / 0.440 | 0.973 | 0.979 | 0.494 | 0.198 |
| ultimatum L40 | - | 0.322 / 0.037 / 0.980 | 0.558 | 0.957 | 0.551 | 0.132 |
| ultimatum L46 | - | 0.454 / 0.037 / 0.980 | 0.697 | 0.982 | 0.563 | 0.142 |

- **What the rule cannot separate.** In this anisotropic space every on-manifold direction shares the few dominant
  components with the n direction: the covariance-matched null is 0.44-0.98, and a random covariance-matched placebo
  also exceeds the isotropic null (L38 cov1: -0.332 below, -0.121 above; L46: -0.881 above). In the WHITENED basis
  (shrinkage 0.1; `whitened_cos_n.json`) the pattern's cosine with the homogeneous n directions is 0.012-0.032 (L38:
  0.017 below, 0.030 above), at the isotropic-null level; probe_clean's is -0.009 to -0.001. Against the all-prompt n slope
  and the frame-matched MoD the pattern is 0.96-0.98 raw: the pattern carries the within-level variation of the
  activations, which in this design is mostly the stimulus. Whether the magnitude test should be raw-vs-isotropic (fires),
  raw-vs-covariance-matched (does not fire at L38), or whitened (does not fire) is Randall's call; the 6b pre-registration
  draft waits on it.

## (5) The G9 vector, and Fan et al.'s construction
- **probe.train (the G9 path) could not run on the 27B**: its `_cfg()` read run.yaml's probe block alone, whose
  layer_candidates [20, 26, 31] are the 9B's, and asked for X_20. Fixed to `modelcfg.probe_cfg()` (the profile's 30/38/40/46).
  It then picks **L38** by CV (held-out level 70: raw 0.986, clean 0.957). `g9_probe_train_vectors.npz`, `g9_probe_train_lottery.json`.
- **The G9 vector is the item 6 vector**: g9_clean = probe_clean at L38 (cos 1.000; same construction), g9_raw cos 0.997.
  Same scale problem: low-variance share 0.89; a 1-sd step moves its worst dimension 67 (clean) / 87 (raw) of its sds; at the
  registered lambda 0.1, 1,394-1,399. G9's steered sweep (probe.calibrate, same unit and grid) would break the model the way
  item 6 did. Also: probe.train chooses the layer by CV over all candidates, so on another run it could choose a layer that
  failed transfer (40/46); nothing in G9 enforces the 2026-10-02.1 transfer verdict. Flagged, not changed.
- **Fan et al. 2026 (arXiv 2609.16436, method section as retrieved):** activations are z-scored before the logistic
  regression; the steering vector is the unit-normalized weight learned on the standardized features, w_hat = w / ||w||,
  added to the hidden state, h' = h + lambda * w_hat (layer 48, the final-token representation). No inverse
  standardization is stated, so as written they steer with the z-space weight in raw activation space: not our raw-space
  filter (w / sd), not the pattern (S w). The units and range of lambda for probe steering, and whether the addition repeats
  on generated tokens, are not stated. Our construction (w / sd) therefore differs from theirs; their vector ("fan" in the
  tables) sits between: low-variance share 0.11-0.15, a 1-sd step moves its worst dimension 10-29 sds, cos with
  probe_clean 0.47-0.69.

## (3) Naturalness: descriptive, whitened cosine added
`probe.naturalness.whitener` / `whitened_cos` (shrinkage covariance, a = 0.1, through the thin SVD); `evaluate` reports
`whitened_cos` beside the raw cosine. Test: identity on itself; two vectors sharing only a dominant axis go from raw 0.96 to
whitened < 0.3.

## (4) First pod check and pins
- `probe.run_steering`: after the instrument checks and lambda-0, the FIRST generation is lambda-0 sampled agreement through
  the KV-cached decode loop (`checks.agreement_lambda0`); STOP on failure, before any sweep.
- Pins: `replay.pins` in both profiles (transformers 5.17.0, nnsight 0.7.0, accelerate 1.15.0, torch 2.8.0, the versions
  on the volume's replay venv); `TorchBackend` STOPs when the installed versions differ; `pod_bootstrap.sh` installs them;
  the 4B smoke runs with them.

## (2) The 6b pre-registration draft: not written (STOP at (1))
Not drafted, because its direction (the pattern) failed the magnitude rule as stated. Ready to draft once the direction
and the magnitude test are decided. Strength unit (natural-projection sd per vector, +-0.25 to +-4 sd), covariance-matched
placebos as the primary null, and coherence-based eligible strengths fixed before any steered output are as Randall specified.
