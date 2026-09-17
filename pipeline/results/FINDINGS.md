# Findings worth carrying into the writeup (Gemma-2-9B-IT, T1 sessions of 2026-09-16)

## Two behavioral fingerprints, as a pair

- **Lottery (safe 50 vs 50/50 gamble):** at T=0 the model chooses Safe for every jackpot up to 50 and
  Risky from 55 on. It switches when the jackpot exceeds the sure amount and ignores the probability
  entirely: probability neglect in its purest form. A risk-neutral agent switches at 100; Fan et al.'s
  Llama-3.3-70B switched at 125. (Artifacts: `t1_2026-09-16_probe/probe/lottery/`.)
- **Ultimatum:** the model accepts every offer from 10 tokens up at T=0: the textbook rational responder.
  (`t1_2026-09-16_probe/probe/ultimatum/`.)

Same model, one task where it runs a heuristic and one where it is homo economicus. That is a stronger
argument for why persona strings are not a population than the usual one, and the probe track surfaced
it at T=0 before doing anything else.

## Reconstruction is blind to scale; identify hooks by identity

A x1.2 copy of the true residual reconstructs slightly BETTER than the true tensor (variance explained
0.768 vs 0.757) because the scaled input just pushes more features over their JumpReLU thresholds; L0
moves instead (59 / 86 / 118 for x0.8 / x1.0 / x1.2). Beating decoys on variance explained shows "best
of the candidates offered", not "right". Tensor identity against an independent implementation
(TransformerLens, no weight processing, fp32: min cosine 0.9999998, norm ratio within 5e-6, resid_pre
cross-check at 0.966) is the warrant; reconstruction is a health check.

## The bf16 flip was numerical

One argmax flip in 46 tokens under bf16 (a 0.13-nat near-tie) vanished in fp32 (70/70 exact across both
paths). Template mismatches produce many flips clustered at the turn boundary; kernel-order noise
produces one at a near-tie. The G1 rule excuses only the latter.

## Published L0 reproduces under a clean protocol

Per-document Pile slices, own BOS, 1024 context, BOS excluded: median L0 86.5 (range 66-101), pooled
85.8 vs the release's 76. A single-BOS concatenation of documents gave 101 (the first document's own L0).

## The lottery trait is linear, generalizes across safe amounts, and is a working dial (probe run 2)

Two-dimensional lottery (safe 30/50/70/100 x jackpot 10..180, order and unit varied), sampled at T=0.8:
- Switching point scales with the safe amount: 38 / 54 (lapse-aware; 66 under a fixed-asymptote fit) /
  95 / 108. The model takes the gamble at roughly 1.1-1.4x the sure thing at every level.
- Logistic probe on the prompt-final residual, trained on safe 30/50/100 and tested on safe **70, a level
  it never saw**: held-out accuracy L20 0.971, L26 0.996, L31 0.986 (Fan et al.: 0.82). The direction is
  the gamble's attractiveness relative to the sure thing, not the digit in the prompt.
- Steering along the unit probe direction at layer 31 (activation addition, fraction of mean residual
  norm): switching point at safe 50 moves monotonically 72.9 (lambda -0.4) -> 24.8 (lambda +0.4), flat
  inside +-0.05, unsaturated at +-0.4; MAE 3.0 tokens on reachable targets (Fan: ~2). The ultimatum has
  no dial on this model at all (rejects only a zero offer), which is itself the fingerprint above.
- A methods point for the fit: the safe-50 curve plateaus at 0.88 (a 9% lapse rate), and a logistic with
  fixed asymptotes put the switching point at 65.5 where the 0.5 crossing was 53. Psychometric fits
  need lapse parameters (Wichmann & Hill); the interpolated crossing is reported beside every fit.

## The plateau is a surface effect, not a lapse rate (provisional, from run 2's own trials)

Well above the switching point the model takes the gamble 100% of the time when the safe option is
listed first, 67% when the risky option is listed first, and 0% when the risky option is listed first
AND the unit word is "tokens" (points: 100% regardless of order; dollars: 91%). The label is therefore
essentially deterministic given (grid point, order, unit): the ceiling on per-trial held-out accuracy is
1.00 by cell versus 0.87 by grid point at the held-out level, so 0.986 / 0.996 are real numbers under a
real ceiling. It also qualifies the probe claim: the direction separates "which option is attractive"
in a way that includes list position and wording, not the gamble's attractiveness alone. The reconciled
version of this finding needs the lambda=0 checksum (served vs steered sampler, 32 seeds, within 2 SE)
to pass first; until then it is provisional.

## The ultimatum "no dial" is a testable prediction

Gemma-2-9B-IT rejects only a zero offer, at any temperature we tried, so it has no fairness-punishment
variable to steer. A persona prompt that produced rejections would be manufacturing behavior with no
internal counterpart: the persona-looks-like-baseline result stated as a prediction, and the cleanest
single argument for why EthosSim characters need measured axes.

## Surface is a treatment (headline)

On the lottery the model flips on option order and on the word "dollars" versus "tokens" more readily
than on the payoff: risky-first + "tokens" -> Safe every time above the switch, safe-first -> Risky every
time. That is Fan et al.'s "slight changes in prompting language lead to big yet unpredictable results"
measured from the inside, and it is the same point as the persona prediction above: for EthosSim, surface
is an uncontrolled treatment in every game unless it is counterbalanced and reported as a marginal. Every
per-grid-point number in this note is therefore reported per surface cell from rules 2026-09-17.1 on,
and the probe direction is orthogonalized against the surface directions before it is called a trait.

## Run 3 (2026-09-17): the dial was mostly framing

Same 2D design, sampled at T=0.8, twelve agents per grid point so every surface cell is sampled at every
lambda; instrument checks first: batched-vs-unbatched log-probs 0.007 nats (fp32+TF32), steered sampler
vs served model at lambda=0 within 2 SE (57.4 +- 4.0 vs 53.7 +- 1.5), and a direct check that steering
moves the first-token log-odds by about +-2 nats at +-0.4 identically across paths and dtypes.

- **Surface leave-one-cell-out.** Raw probe: 0.93-0.94 where framing does not matter, 0.58 on
  risky_first/tokens (ceiling 0.91) and 0.71 on safe_first/points. The raw direction does not generalize
  to an unseen framing exactly where framing drives the choice.
- **Orthogonalized direction** (surface difference-of-means matched on grid point AND label projected out;
  cosine 0.990 with the raw): held-out level 70 = 0.804 vs raw 0.986, against a grid-point ceiling of
  0.871. Per cell: 0.95-0.98 where framing does not matter, 0.38 in risky_first/tokens — below chance,
  because there the behavior IS the framing and a trait-only direction predicts the opposite.
- **Dials, per surface cell, effect = sp(+0.4) - sp(-0.4) at safe 50:**

  | cell | raw | clean |
  |---|---|---|
  | risky_first/dollars | -11 | -2 |
  | risky_first/points | -39 | +2 |
  | risky_first/tokens | -53 | -38 |
  | safe_first/dollars | -4 | +2 |
  | safe_first/points | -43 | -16 |
  | safe_first/tokens | +3 | -11 |
  | **median** | **-25** | **-6** |

  Two agents per cell per grid point, so single cells carry wide error bars; the pattern is the finding.
  Run 2's 73 -> 25 pooled swing was the raw direction moving the framing-linked component in a cell mix
  weighted toward the responsive cells. After cleaning, the trait component moves the switching point by a
  few tokens, inconsistently across cells: on this task, for this model, most of the steerable variance was
  framing. That is a result, and it is the one the surface headline predicted.
- **What survives.** The T=0.8 baselines still scale with the safe amount (sp/safe ~1.05 median over 22
  level x cell combinations, range 0.96-3.14 with the tokens cells at the top); the per-layer held-out
  ordering (L26 >= L31 > L20) holds for the raw probe; and the model's ultimatum behavior has no dial.
