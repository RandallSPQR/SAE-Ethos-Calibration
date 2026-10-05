# Item 6 steering (gate rules 2026-10-03.1)

**G4 (lottery_L38, probe_clean, served readout): NOT_EVALUABLE** (an instrument check failed); sensitivity (untruncated softmax): NOT_EVALUABLE

Instrument checks all passed: False

## lottery_L38 (primary; 16 placebos; G4)

Naturalness: personas valid True (dP 0.938); null p99 |cos| 0.633; probe_clean cos 0.136 [0.135, 0.137] FAIL

Transfer (2026-10-02.1): probe_clean 0.839 PASS

| vector | readout | lambda* | E per cell | median E | pooled E [95 %] | sign agree | beats every placebo | verdict |
|---|---|---|---|---|---|---|---|---|
| probe_clean | served | - |  | - | - [-, -] | - | - | **NOT_EVALUABLE (an instrument check failed)** |
| probe_clean | softmax | - |  | - | - [-, -] | - | - | **NOT_EVALUABLE (an instrument check failed)** |

Dose curve, pooled sp (served): lambda: target / placebo mean / coherent
- probe_clean: -0.8: -/34.8/n  -0.6: -/105.0/n  -0.4: -/54.0/n  -0.2: -/65.4/n  -0.1: -/65.9/n  0.0: 65.0/65.0/y  0.1: -/65.3/n  0.2: -/77.3/n  0.4: -/67.1/n  0.6: -/105.0/n  0.8: -/-/n

Per-item shifts at lambda* (served; placebo-subtracted d_i): quantiles 5/25/50/75/95, moved, wrong way, at-target-side mean

## lottery_L30 (secondary; 4 placebos; DESCRIPTIVE, gates nothing)

Naturalness: personas valid True (dP 0.938); null p99 |cos| 0.585; probe_clean cos 0.178 [0.175, 0.179] FAIL

Transfer (2026-10-02.1): probe_clean 0.865 PASS

| vector | readout | lambda* | E per cell | median E | pooled E [95 %] | sign agree | beats every placebo | verdict |
|---|---|---|---|---|---|---|---|---|
| probe_clean | served | - |  | - | - [-, -] | - | - | descriptive (4 placebos): criteria not_evaluable (an instrument check failed) |
| probe_clean | softmax | - |  | - | - [-, -] | - | - | descriptive (4 placebos): criteria not_evaluable (an instrument check failed) |

Dose curve, pooled sp (served): lambda: target / placebo mean / coherent
- probe_clean: -0.8: -/-/n  -0.6: -/-/n  -0.4: -/-/n  -0.2: -/-/n  -0.1: -/82.8/n  0.0: 65.0/65.0/y  0.1: -/59.0/n  0.2: -/-/n  0.4: -/-/n  0.6: -/-/n  0.8: -/-/n

Per-item shifts at lambda* (served; placebo-subtracted d_i): quantiles 5/25/50/75/95, moved, wrong way, at-target-side mean

## ultimatum_L40 (exploratory; 4 placebos; DESCRIPTIVE, gates nothing)

Naturalness: personas valid True (dP 0.899); null p99 |cos| 0.560; probe_clean cos 0.054 [0.050, 0.058] FAIL

Transfer (2026-10-02.1): probe_clean 0.866 PASS

| vector | readout | lambda* | E per cell | median E | pooled E [95 %] | sign agree | beats every placebo | verdict |
|---|---|---|---|---|---|---|---|---|
| probe_clean | served | - |  | - | - [-, -] | - | - | descriptive (4 placebos): criteria not_evaluable (an instrument check failed) |
| probe_clean | softmax | - |  | - | - [-, -] | - | - | descriptive (4 placebos): criteria not_evaluable (an instrument check failed) |

Dose curve, pooled sp (served): lambda: target / placebo mean / coherent
- probe_clean: -0.8: -/-/n  -0.6: -/0.4/n  -0.4: -/15.0/n  -0.2: -/7.4/n  -0.1: -/5.8/n  0.0: 3.6/3.6/y  0.1: -/4.1/n  0.2: -/8.9/n  0.4: -/43.1/n  0.6: -/-/n  0.8: -/-/n

Per-item shifts at lambda* (served; placebo-subtracted d_i): quantiles 5/25/50/75/95, moved, wrong way, at-target-side mean

## ultimatum_L46 (exploratory; 4 placebos; DESCRIPTIVE, gates nothing)

Naturalness: personas valid True (dP 0.899); null p99 |cos| 0.558; probe_clean cos 0.072 [0.068, 0.076] FAIL

Transfer (2026-10-02.1): probe_clean 0.841 PASS

| vector | readout | lambda* | E per cell | median E | pooled E [95 %] | sign agree | beats every placebo | verdict |
|---|---|---|---|---|---|---|---|---|
| probe_clean | served | - |  | - | - [-, -] | - | - | descriptive (4 placebos): criteria not_evaluable (an instrument check failed) |
| probe_clean | softmax | - |  | - | - [-, -] | - | - | descriptive (4 placebos): criteria not_evaluable (an instrument check failed) |

Dose curve, pooled sp (served): lambda: target / placebo mean / coherent
- probe_clean: -0.8: -/-/n  -0.6: -/-/n  -0.4: -/0.3/n  -0.2: -/4.1/n  -0.1: -/3.6/n  0.0: 3.6/3.6/y  0.1: -/6.9/n  0.2: -/5.3/n  0.4: -/12.6/n  0.6: -/-/n  0.8: -/-/n

Per-item shifts at lambda* (served; placebo-subtracted d_i): quantiles 5/25/50/75/95, moved, wrong way, at-target-side mean

G4 is an INSTRUMENT gate: a PASS shows that steering works in this pipeline, not that probe_clean is a risk-preference variable (PREREG_ITEM6_STEERING.md section 6).

Reading: steering is judged on behavior minus placebo, never on probe readout. The served readout decides; the untruncated softmax is a sensitivity readout (top-p can drop an option from the nucleus and put a step in the dose curve). Naturalness of the lottery personas includes lexical priming: personas 1, 3 and 4 share vocabulary with the option text.
