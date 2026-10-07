# Item 6b steering (gate rules 2026-10-06.1)

**G4 (the sole confirmatory test: D at lottery L38, served readout): NOT_EVALUABLE** (no symmetric eligible strength <= 2 with every cell's sp inside the grid for the target and every placebo); k* = None sd; pooled E - [-, -]; median E -; softmax sensitivity NOT_EVALUABLE

- D: sha 1441d647172eb58a, sd_v 507.0, worst-dim push of a 1-sd step 2.00 (STOP > 2.93), low-variance share 0.038, token-direction share removed 0.001
- D_word: sha 277a83d6bf8abb2e, sd_v 593.9, worst-dim push of a 1-sd step 13.24 (STOP > 2.93), low-variance share 0.036, token-direction share removed 0.002
- cos(D, D_word) 0.328

Overlap of the training prompts with every evaluation set: {'g4': 0, 'manipulation': 0, 'coherence_lottery': 0, 'relabeled': 0}

## D (G4 (sole confirmatory test))

Eligibility by |k|: 0.25: eligible; 0.5: eligible; 1.0: eligible; 2.0: eligible; 4.0: eligible

- served: NOT_EVALUABLE (no symmetric eligible strength <= 2 with every cell's sp inside the grid for the target and every placebo); k* None; median E -; pooled E - [-, -]; vs isotropic pooled -
- softmax: NOT_EVALUABLE (no symmetric eligible strength <= 2 with every cell's sp inside the grid for the target and every placebo); k* None; median E -; pooled E - [-, -]; vs isotropic pooled -
- manipulation (acc / mean stated amount / verdict) by k: -4.0: 1.00/70.0/PASS  -2.0: 1.00/70.0/PASS  -1.0: 1.00/70.0/PASS  -0.5: 1.00/70.0/PASS  -0.25: 1.00/70.0/PASS  0.25: 0.99/69.7/PASS  0.5: 0.99/69.7/PASS  1.0: 0.99/69.7/PASS  2.0: 0.99/69.7/PASS  4.0: 1.00/70.0/PASS

## n_direction (descriptive)

Eligibility by |k|: 0.25: eligible; 0.5: eligible; 1.0: eligible; 2.0: eligible; 4.0: eligible

- served: NOT_EVALUABLE (no symmetric eligible strength <= 2 with every cell's sp inside the grid for the target and every placebo); k* None; median E -; pooled E - [-, -]; vs isotropic pooled - (descriptive)
- softmax: NOT_EVALUABLE (no symmetric eligible strength <= 2 with every cell's sp inside the grid for the target and every placebo); k* None; median E -; pooled E - [-, -]; vs isotropic pooled - (descriptive)
- manipulation (acc / mean stated amount / verdict) by k: -4.0: 1.00/70.0/PASS  -2.0: 1.00/70.0/PASS  -1.0: 1.00/70.0/PASS  -0.5: 1.00/70.0/PASS  -0.25: 1.00/70.0/PASS  0.25: 0.99/69.7/PASS  0.5: 1.00/70.0/PASS  1.0: 1.00/70.0/PASS  2.0: 1.00/70.0/PASS  4.0: 1.00/70.0/PASS

## fan (descriptive)

Eligibility by |k|: 0.25: eligible; 0.5: eligible; 1.0: eligible; 2.0: eligible; 4.0: coherent

- served: NOT_EVALUABLE (no symmetric eligible strength <= 2 with every cell's sp inside the grid for the target and every placebo); k* None; median E -; pooled E - [-, -]; vs isotropic pooled - (descriptive)
- softmax: NOT_EVALUABLE (no symmetric eligible strength <= 2 with every cell's sp inside the grid for the target and every placebo); k* None; median E -; pooled E - [-, -]; vs isotropic pooled - (descriptive)
- manipulation (acc / mean stated amount / verdict) by k: -4.0: 1.00/70.0/PASS  -2.0: 1.00/70.0/PASS  -1.0: 1.00/70.0/PASS  -0.5: 1.00/70.0/PASS  -0.25: 1.00/70.0/PASS  0.25: 0.99/69.7/PASS  0.5: 0.99/69.7/PASS  1.0: 1.00/70.0/PASS  2.0: 1.00/70.0/PASS  4.0: 0.97/69.2/PASS

## Relabeling cross-check (descriptive)

not evaluable: G4 has no k*

G4 is an instrument gate: a PASS shows steering works in this pipeline, not that D is a risk-preference variable.
