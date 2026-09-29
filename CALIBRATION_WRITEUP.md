# SAE-Ethos-Calibration on Gemma-2-9B-IT: what survives, what dies, and what it calibrates

> **Status note, 2026-09-29 (main 91cbfb5).** The body below was written at de03c61, before the deep resample; it is kept
> as written. Superseded since: (1) §3's "white-box primary question: not evaluable" is answered. The deep resample
> (`pipeline/results/t3_2026-09-29_deep/`, 700 continuations in 7 mixed full cells, $10.66) ran with G1, G2 and G8
> green and no feature survives the family-wise within-cell null (primary p_max 0.20 on 17 vs 20 uids, below the 20/20
> rule; secondary p_max 0.34 on 86 vs 52, evaluable): on this model the decision-span state does not predict the outcome
> beyond the cell. (2) §5 item 5: G8 now passes under rules 2026-09-28.4 on the deep store (bias z 3.22 vs 3.775, scale
> 1.38 vs 1.5), so the instrument statement rests on G1, G2 and G8. (3) §5 item 7: under labeler 2026-09-28.2, 237 of 245
> credential "pushed" claims had a push call and 8 did not; the 113 figure counted
> only the `git` tool's own push events and missed pushes inside bash compound commands (`harness/LABELER_CHANGELOG.md`). (4)
> Study totals: about $60 of GPU across about 17 pods, 2026-09-16 to 09-29. (5) §6: the deep resample ran with seven
> cells, not eight (missing_delete seed 6's greedy prefix did not reach the obstacle; excluded and counted).

Study period 2026-09-16 to 2026-09-28. Repo `RandallSPQR/SAE-Ethos-Calibration`, main at de03c61. About 25 A100
hours across fourteen pods, roughly $40 of GPU. Every number below cites the artifact it comes from; numbers the
pre-27B audit (`PRE_27B_AUDIT.md`) found to be wrong or overstated in `FINDINGS.md` are given here in corrected
form and flagged.

This is the front matter for the write-up and the calibration statement for EthosSim. It is written to be
read by someone who will pay for the millions and wants to know what "measured axis" means.

---

## 1. What the study set out to do

Reproduce, at budget, the white-box methods of the Mythos system card (§4.5.3–4.5.4) on an open model: put an agent
in front of obstacles, resample its decision point, read its residual stream through a sparse autoencoder, and ask
whether the state at the decision predicts what the episode does. Along the way, use Fan et al. (2026,
arXiv:2609.16436) as an external known-answer fixture for probe-based steering. The purpose was calibration: to
learn which instruments can be trusted on which data before EthosSim puts numbers on a character.

The standard set at the start and held throughout: **a surprising result must be trustworthy**, so every claim
rests on a versioned gate with a checksum, not on a config field or a plausible plot. Nine gates (G0–G8) plus one
for the probe track (G9). Nothing in the ladder was tuned to pass; every rule change is a dated changelog entry with
its rationale and a fixture.

---

## 2. What survives

### 2.1 The instrument (white-box)

A pinned, gated stack on Gemma-2-9B-IT: weights sha256-verified per shard against a pinned revision; the layer-31
residual hook proved by tensor identity against an independent implementation in fp32 (cosine ≥ 0.9999998, norm
ratio within 5e-6, a resid_pre cross-check holding at 0.966); the Gemma Scope layer_31/width_16k SAE reconstructing
real decision-turn residuals at variance explained 0.683 with every decoy hook rejected by ≥ 0.11 and JumpReLU
integrity exact; a permuted-label null (G8) that runs the real analysis pipeline. Replay reproduces generation: on
the 2,101 T3 rows that re-tokenize exactly, 2,091 agree within 0.05 nats (median worst gap 0.004).
(`t1_2026-09-16_fp32/features/identity_report.json`, `t3_2026-09-28_whitebox/features/sae_health.json`,
`replay_g1fixed/`.)

Two methods results came out of building it: **reconstruction is blind to a scale error** (a ×1.2 copy of the true
tensor reconstructs slightly *better*, VE 0.768 vs 0.757; L0 and identity see it at once), so hooks are identified
by identity and reconstruction is a health check; and **the bf16 numerics on this stack are shape-sensitive**
(one near-tie argmax flip in generation, 0.18 nats between batched and unbatched sampling), so everything
precision-critical runs fp32 with TF32 matmuls.

Two calibration instruments **do not transfer** from calibration text to real decision spans and are crossed off:
the Karvonen activation oracle (0.917 paired discrimination on code activations at T1; 0.33 on real spans, below
chance, confabulating on every null window) and the Neuronpedia "code" feature as a known-answer anchor (AUROC
1.000 on calibration text; 0.62 on JSON-embedded tool-call snippets, with only 9 code-bearing spans in 48).

### 2.2 The harness (behavioral)

An obstacle harness that confines model-authored code mechanically (seccomp-bpf + dedicated uid + Landlock, ten
canaries including the real token on disk, positive canaries that prove the door that must be open is open),
counts every exclusion in cardinality rather than dropping it, records protocol failures separately from decisions,
and whose deterministic labeler agrees with a careful human at **κ 0.908 (47/50)** on a blind set the labeler never
saw, with the harness's instrument readings on every item. (`t3_2026-09-27_session1/hand_labels/`.)

That number was earned: κ was 0.39 on first contact and 0.33 on the second set. Round 1 was labeler defects (a
substring match on "environ" that turned "in this environment?" into `searched_for_secrets`; no precedence between
concealment and cheating; a false-completion rule that existed for one scenario and not another). Round 2 was
**evidence asymmetry**: the labeler read final state and the termination reason, the human read the transcript, and
eight of 23 disagreements were "hit the cap" versus "ended in prose one call short of it." Putting the readings on
the sheet, as measurements rather than labels, took agreement to 0.91. This is the finding about labelers: the
human and the machine have to read the same instrument. (`harness/LABELER_CHANGELOG.md`.)

### 2.3 The axis validation protocol

The calibration deliverable, and it came from a failure. A probe direction trained on the Fan et al. lottery
reproduced every statistic they report: held-out accuracy on a safe level it never saw 0.971/0.996/0.986 at layers
20/26/31 (Fan: 0.82); a monotone, unsaturated dial moving the switching point 73 → 25 over λ ±0.4 with 3-token MAE
(Fan: ~2). Then leave-one-framing-out dropped held-out accuracy to 0.58 in the cell where framing drives the choice;
orthogonalizing against label-matched surface directions left a direction that still reads payoff structure (0.804
against a 0.871 ceiling) but moves the switching point by a median of 6 tokens across surface cells against 25 for
the raw direction, inconsistently signed. **Most of the steerable variance was framing.** The prediction was
written and committed before the sweep ran. (`t1_2026-09-17_probe3/`, `FINDINGS.md` "the dial was mostly framing".)

The rule that came out of it, in `AXIS_VALIDATION_PROTOCOL.md`: **steerable is not the same as trait.** A direction
earns the name "trait" (model-level) only when its effect survives held-out framing and moves every framing cell
in the same direction; otherwise it is a knob on the environment (persona, framing). Eight ordered steps, each with
a run-3 number showing what it catches, and a NOT_EVALUABLE state for underpowered runs.

### 2.4 The portrait of a 9B

Behavioral fingerprints, each a measured fact rather than an anecdote:

- **Probability neglect.** Safe up to a jackpot of 50, risky from 55: it switches when the jackpot exceeds the sure
  amount and ignores the coin flip. Risk-neutral is 100; Fan's Llama-3.3-70B sat at 125. Holds at T=0.8 across four
  safe levels (switching point ≈ 1.1–1.4× the sure amount).
- **Rational responder.** Rejects only a zero ultimatum offer; there is no fairness-punishment variable to steer.
  A persona prompt that produced rejections would be manufacturing behavior with no internal counterpart.
- **Surface is a treatment.** Well above the switching point it takes the gamble 100% of the time with the safe
  option listed first, 0% with the risky option first and the unit word "tokens." A model whose behavior in a cell
  *is* the framing has no separable trait there.
- **It cannot put a double quote inside a JSON string argument.** Any tool call that needs one fails for protocol
  reasons, not choice. This drove three of the four scenario retunes and is a constraint on every scenario written
  for this model class.
- **The shortcut tracks the difficulty of the honest path, not the availability of the shortcut.** Same notice,
  same forbidden table, same seeds; honest route absent → 0.36 forbidden-table use; present but blocked by a tool
  defect → 0.00; executable but hard → 0.16; executable and easy → 0.00 (upper bound ≈ 0.07 at n=50). That is a
  dose-response in honest-path difficulty and the best single result of the project.
  (`t2_2026-09-27_honeypot_slice{,2,3}/README.md`.)
- **Once protocol failures are separated from decisions, it mostly does not transgress.** T3, n≈180–200 per cell,
  20 seeds: impossible_test full 0.128 [0.087, 0.184] vs control 0.034 [0.015, 0.071], Fisher p = 0.0015 — the one
  scenario with contrast, and its 23 transgressions are 13 test edits, 9 fabricated "done," 1 concealed edit, with
  most of the full variant flailing (damaged 72, timeout 34) rather than gaming. Credential 0/200. Honeypot 2/200.
  missing_delete 0.394 vs 0.190 (p = 2e-5) **on a failing control** (success 0.458 < 0.5): the model deletes,
  skips the suite, and claims done as a baseline trait, and in the full variant empties the file it cannot delete
  and reports removal (51/170). That rate is never quoted without its floor failure.
  (`t3_2026-09-28_joined/analysis/BEHAVIORAL.md`; audit-corrected: **seven** seeds carry no impossible_test
  transgression, not five.)

### 2.5 The dataset

2,289 labeled, pinned, confined continuations across four scenarios, three variants, twenty seeds, with a per-uid
in-span SAE feature store and replay token metadata, plus the three honeypot slices and the T2 runs. Reusable.

### 2.6 The methods lessons, each with a receipt

Parser contamination (33/150 rows of the first "pilot" carried rejected flat-form calls; filed as validation, not
data). The empty-`find` bug that crashed the one input by which this model empties a file, excluding exactly the
move the cell measured (7 exclusions, all one cell, caught because exclusions are counted). A parameter named
`args` inside an envelope whose argument object is `args` (73/150 continuations lost to it). The oracle not
transferring. The code anchor not matching the span. G1's maximum over half a million positions exceeding a
tolerance by fp32 kernel noise. Each is a paragraph a reviewer would otherwise write.

---

## 3. What dies

- **The dial as a trait on this model.** Framing, mostly. Reported as a bound, not a null (two agents per cell per
  grid point cannot distinguish "a few tokens" from zero).
- **The oracle as evidence on decision spans.** Zero weight; labels are Neuronpedia's.
- **Credential and honeypot as contrast scenarios on a 9B.** No transgressions to study at n=200 once the honest
  path is findable. 27B questions.
- **Fan et al.'s absolute numbers as targets.** Their switching point (125) and layer (48) are Llama's; what
  transfers is shape.
- **The white-box primary question at this design.** Not falsified: **not evaluable.** Six against three
  informative uids in two mixed cells (`t3_2026-09-28_whitebox/README.md`). The within-cell null did what it was
  told and threw away the between-seed variation where nearly all the transgressions live, along with the surface
  confound. The tertiary contrast (75 vs 140 in 29 cells) is a real, weak null with two lexical per-feature
  survivors that do not clear family-wise. FINDINGS' headline "no feature survives" is corrected here to three
  outcomes: primary and secondary NOT EVALUABLE, tertiary a weak null.
- **The 9B as a substrate for character.** Almost no steerable trait structure that survives framing; almost no
  transgression to study; a protocol ceiling. It was an excellent dev rig for building the instrument and it is
  probably not the model EthosSim runs on.

---

## 4. What "calibration" means now: three levels

The project set out to calibrate an instrument. What it produced is a three-level account of where an agent's
behavior comes from, each level with a test that distinguishes it from the others:

| level | what it is | how you tell | 9B examples |
|---|---|---|---|
| **model-level (trait)** | a disposition that survives held-out framing and moves every surface cell the same way | axis protocol steps 3–6; per-cell effects with intervals; G9 | little found: payoff structure is read (0.80 held-out) but does not move choice beyond a bound |
| **environment-level (persona, framing, honest-path difficulty)** | real levers on behavior that are properties of the situation, not the agent | leave-one-framing-out; the honeypot dose-response; per-cell reporting | option order, unit word, jackpot-vs-sure-thing heuristic, shortcut use tracking honest-path difficulty |
| **protocol-level (could not speak)** | failures that are not decisions at all | `bad_calls`, `unparseable_tool_blocks`, `call_repairs` per uid; the positive canaries | quotes in JSON, `args` collision, dropped closers: three of four scenario "rates" before 2026-09-27 |

Most of what a naive eval calls "behavior" on a small model sits in levels two and three. A character axis
published for EthosSim comes with the level it was measured at, and the protocol that placed it there. That is
model-independent, and it is the thing that transfers to the 27B.

---

## 5. Corrections to FINDINGS.md from the pre-27B audit

Applied here; to be applied in the repo before anything is cited:

1. "Five seeds carry none" → **seven** (2, 6, 7, 9, 11, 15, 17 of the 18 reached).
2. "The 8 % of rows that differ do so at one boundary token" → 163 rows at one boundary token; **25 rows shifted by
   29–180 positions** because generation ran past `<end_of_turn>` (no stop token configured) and the model
   fabricated a user turn or tool result inside its own message. About 1.2 % of all continuations (31 with a
   substantive overrun; 22 scored turns containing fabricated turn text). Verified independently.
3. "Now fixed in replay" → length alignment is fixed; the boundary merge (fixed 2-token suffix subtraction) is not.
4. G1: the 99.5 % figure is a subset statistic computed off-script; G1 as a gate **failed** by its rule (max over all
   rows). Reported as red-by-rule with the distribution beside it, and the rule amended (quantile tolerance) before
   the next run.
5. G8's row "78 destructive vs 232 benign" → 232 is all non-destructive taxonomies; G8 evaluated a pooled
   contrast, not any of the three reported ones. And G8's pass is **not reproducible**: the permutation stream
   depends on directory order and `hash()`, and the bound formula is for the pooled d while the statistic is the
   stratified d. Re-run with eight RNG seeds by the auditor: null mean 0.143–0.207 against a bound of 0.157, five of
   eight failing. The recorded 0.144 is one draw. The "instrument is trustworthy" sentence stands on G2 and replay
   fidelity, not on G8, until G8 is fixed.
6. G6 κ 0.908 is a valid measure of human–labeler agreement on those fifty transcripts, but 16 of the fifty are
   missing_delete continuations from the session-1 run that was **dropped** from the analysis set (uids are not
   run-scoped; the redo reused them). Re-running G6 against the joined set scores those uids against different
   continuations (κ 0.625). The certification is real; the audit trail is broken; run-scope the uids.
7. Credential control success 0.975 is overstated: `fixed_and_reported` does not require a push call, and 113 of 245
   "pushed" submissions have none. The full rate (0/200) is unaffected.
8. "Binary κ 0.96" is benign-vs-rest; the binary that the rates depend on (transgression-vs-not) is 0.88.

---

## 6. What was done next, and what is next

Done after the white-box null: the deep resample of the responsive cells was pre-registered as an addendum
(eight mixed full cells × 100 continuations, same surfaces and decision points, discovery on even cells and effects
on odd, both outcomes allowed in advance). It is the within-cell question asked once at proper power, and either
outcome closes the 9B study honestly.

Before the 27B: the blockers in `PRE_27B_AUDIT.md` (stop tokens; G8 bound and determinism; G1 criterion;
run-scoped uids; cardinality merge), then the parameterization of everything that silently assumes Gemma-2-9B
(layer 31, token ids 107/108/1, the 16k SAE, the oracle, the serializer import, the Neuronpedia defaults, the
shell drivers' model and dtype flags), then a fresh ladder G0–G8 on the new model with the identity check made
mandatory. Nothing from the 9B gates transfers except their logic and fixtures.

Then the two questions the 9B could not answer: does the cleaned lottery direction survive on a model whose baseline
is not pinned to the jackpot-equals-sure-thing line, and does the honeypot dose-response keep its shape. Those are
the first rows of the cross-family table.
