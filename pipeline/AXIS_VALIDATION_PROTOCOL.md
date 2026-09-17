# Axis validation protocol: when a steerable direction may be called a trait

Written 2026-09-17 from probe run 3 (`pipeline/results/FINDINGS.md`, "the dial was mostly framing"), for
EthosSim and for any character built on measured axes. It is what will be asked for when someone pays
for the millions.

## The rule

**Steerable is not the same as trait.** A direction earns the name "trait" only when its effect survives
held-out framing and moves every framing cell in the same direction. Until then it is a knob, and the
knob may be turning the framing, not the disposition.

Run 3 is the existence proof: a direction that was calibrated, monotone, unsaturated, with a 3-token MAE
on its targets and 0.986 held-out accuracy on a safe level it never saw, would have passed every pooled
statistic Fan et al. (2026) report. Orthogonalized against the surface directions and read per cell, most
of its effect was list order and unit wording.

## The protocol, in order

Each step is cheap next to the one after it, and each catches something the earlier ones cannot.

1. **Vary the surface on purpose.** Every trial carries a surface condition (option order, unit word,
   any wording that is not the construct), counterbalanced across the grid so every grid point sees every
   cell. Surface is a treatment in every game; if it is not varied it is confounded, and if it is not
   recorded it cannot be reported.
2. **Hold out on the construct.** Train the probe on some levels of the construct (safe 30/50/100) and
   test on a level it never saw (70). A direction that transfers to an unseen level found something
   closer to a trait than a digit reader. This is Fan et al.'s check, and it is necessary, not sufficient.
3. **Hold out on the framing (leave-one-cell-out).** Train on all surface cells but one, test on the one
   left out. Report the minimum over cells and the cell that produced it. Run 3: 0.93 to 0.94 where framing
   did not matter, 0.58 in the cell where framing drove the choice. The raw direction fails to generalize
   exactly where the behavior is the framing.
4. **Orthogonalize, matched on label.** Estimate each surface direction as a difference of means at matched
   grid point AND matched label, project them out of the probe weight, and store the cleaned direction
   beside the raw one. Matching on label matters: grid-only matching strips trait signal with the surface
   (run 3: cleaned held-out 0.71 versus 0.80 label-matched). Report the cosine between raw and clean; a
   high cosine with a large behavioral difference is itself a finding (run 3: 0.990).
5. **Instrument checks before any steered claim.** The steered sampler at lambda=0 reproduces the served
   model's unsteered curve within 2 bootstrap SE; a batched path matches the unbatched path at the G1
   log-prob tolerance AND shows the steering effect; a direct log-odds diagnostic shows the injection
   moves the first token the same way on every path and dtype. Run these first, so a null cannot be blamed
   on the instrument afterwards.
6. **Steer both directions, report per cell, gate on the cleaned one.** The pooled psychometric curve is
   a mixture across cells and is descriptive only. The claim is the per-cell effect of the cleaned
   direction, sp(+lambda) - sp(-lambda), with a bootstrap interval, and it passes only when: the sign
   agrees in at least five of six cells; the median per-cell |effect| clears a threshold set in the
   construct's units (10 tokens on the lottery); and every cell's interval excludes zero. The raw
   direction's per-cell effects are reported beside it so the reader can see how much was framing.
7. **Power is a precondition, not a footnote.** If per-cell n is too small for the interval to mean
   anything, the answer is NOT EVALUABLE, never pass or fail. Two trials per cell per grid point is that
   state. The pipeline's G9 returns it as a third status and it blocks spend like a fail. State the
   result of an underpowered run as a bound ("under roughly 10-15 tokens in most cells"), not a null.
8. **Write the prediction down before the run, with the time.** Run 3's outcome was predicted by the
   surface headline written at 09:55 EDT on 2026-09-17 (commit c0c182c), from run 2's trials, before the
   sweep ran at 10:37. A dated prediction is the difference between "we found it" and "we fitted it".

## What a failure means

A direction that fails at step 3 or 6 is not useless; it is a framing knob, and the size of its raw
effect measures how much of the behavior on that task is framing. A model whose behavior in a cell IS the
framing (run 3's 0.38 cell, below chance for the cleaned direction) has no separable trait there: trait
and framing are the same computation. That is a fingerprint of the model, and it is worth more to a
character sheet than a dial that was never validated.

## What this buys EthosSim

A character axis published with this protocol comes with: the surface cells it was validated across, its
held-out-framing accuracy, the cosine between raw and cleaned directions, per-cell effects with intervals,
the dated prediction, and a gate status. A character axis published without it is a persona string with
a number attached.
