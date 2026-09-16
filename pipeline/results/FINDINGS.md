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
