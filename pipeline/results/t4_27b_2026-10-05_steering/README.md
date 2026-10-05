# Item 6 steering, 27B (gate rules 2026-10-03.1): G4 = NOT_EVALUABLE

Pre-registration: `analyze/PREREG_ITEM6_STEERING.md`. Frozen vectors: `results/t4_27b_2026-10-03_steering_vectors/` (sha256
302557c3...). The offline re-score of `steering/` reproduces the pod's verdict (`steering/STEERING.md`, `steering.json`).

## Runs and cost
| pod | code | window (UTC, 2026-10-05) | what happened | cost |
|---|---|---|---|---|
| o7nletg11eogbp | 5c46982 | 16:48 - ~17:28 | preflight, batch gate (0.048 / 0.009, tol 0.05), path check, lambda-0 (lottery 88.4 vs 88.5 +- 4.0; ultimatum 4.9 vs 5.5 +- 1.2) all passed; then `hf.generate` raised under transformers 5.17 (local smoke had used 4.57); the pod's stall guard terminated it | ~$2.1-2.3 |
| a5pd8lxb54k7km | afc78a1, then 219c9f9 | 20:18 - 21:57 | resumed after the checks; timing 0.048 s/prompt (projected 71 min); L38 sweep done; a sampled-agreement crash on cells with no parseable answer (guard added, 219c9f9); resumed to DONE | $5.21 |
Item 6 total ~$7.4 (cap $11.13). Code changes between pods: the KV-cached decode loop (afc78a1) and the empty-cell guard
(219c9f9); no rule, vector or readout changed.

## Result under the registered rules
**G4 (lottery L38, probe_clean, served): NOT_EVALUABLE**, sensitivity (softmax) the same. The instrument check that failed
is sampled agreement: at lambda = +-0.4 none of 1,260 sampled answers parsed (option mass 0), so agreement could not be
shown. Independently, no strength is coherent for probe_clean at any site, so no lambda* exists either.

Mean served option mass (P(Safe or Risky / Accept or Reject) under the served distribution), lottery L38:

| lambda | -0.8 | -0.6 | -0.4 | -0.2 | -0.1 | +0.1 | +0.2 | +0.4 | +0.6 | +0.8 |
|---|---|---|---|---|---|---|---|---|---|---|
| probe_clean | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| isotropic placebos (mean of 8) | 0.00 | 0.00 | 0.00 | 0.41 | 1.00 | 0.97 | 0.45 | 0.00 | 0.00 | 0.00 |
| covariance-matched placebos (mean of 8) | 0.01 | 0.17 | 0.98 | 1.00 | 1.00 | 1.00 | 1.00 | 0.90 | 0.14 | 0.01 |

The same ordering holds at L30 and both ultimatum sites (descriptive; 4 placebos each). probe_clean's coherence texts are
degenerate at every strength (e.g. +-0.1: ppl ratio 10-353, repeated 4-grams 0.40-0.89: "'si1111...", mixed-script loops).

## Why (post-hoc diagnostic, descriptive: `probe/steer_diagnose.py`, `diagnose_scale.json`)
probe_clean is the logistic weight mapped to raw space (w / sd), which concentrates on low-variance dimensions:

| site | share of |v|^2 on the 10 % lowest-variance dims: probe_clean / iso / cov | max per-dim push at lambda 0.1, in that dim's own sds: probe_clean / iso / cov | lambda for a 1-sd push along v: probe_clean / iso / cov |
|---|---|---|---|
| lottery L38 | 0.895 / 0.101 / 0.009 | 1,399 / 153 / 5 | 0.0048 / 0.0008 / 0.037 |
| lottery L30 | 0.739 / 0.096 / 0.008 | 3,342 / 460 / 26 | 0.0010 / 0.0001 / 0.007 |
| ultimatum L40 | 0.921 / 0.101 / 0.012 | 3,435 / 174 / 8 | 0.0031 / 0.0007 / 0.026 |
| ultimatum L46 | 0.921 / 0.097 / 0.007 | 1,068 / 141 / 5 | 0.0047 / 0.0008 / 0.042 |

The max-per-dim push orders the breakdown exactly (cov survives to +-0.4, iso breaks at +-0.2, probe_clean at +-0.1). A
logistic weight is a decoding FILTER; the direction along which the activations actually vary with the class is its
activation PATTERN (Haufe et al. 2014, a = S w). The registered unit (fraction of the mean residual norm) along a filter
direction is off-manifold at the smallest registered strength. This is an instrument result, not evidence about whether
the choice is steerable.

## Other observations
- Naturalness: the personas move behavior strongly (dP 0.94 lottery, 0.90 ultimatum; valid). probe_clean's cosine with
  the persona-induced shift is 0.05-0.18 (< 0.20). The covariance-matched null's 99th percentile is 0.56-0.63, not ~0.035:
  in this anisotropic space random covariance-matched directions and the persona shift share the dominant components, so
  the D3 "above the null" clause is close to unreachable for any vector. Worth revisiting before naturalness gates anything.
- lambda-0: the exact readout reproduces the served curve on both tasks (logistic-lapse estimator, as registered).
