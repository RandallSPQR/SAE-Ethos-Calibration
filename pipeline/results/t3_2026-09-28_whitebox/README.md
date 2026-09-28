# T3 white-box session, 2026-09-28 — replay of the joined T3 set (run_febbc90c06ea + run_64df1ae8a9a0)

**AUDIT 2026-09-28 (see `AUDIT_RESPONSE.md`): the replay under this store mis-tokenized 8 % of decision spans and
mis-cut 25 (interior end-of-turn); G1 FAILS as a gate on it; the G8 pass reported below was not reproducible across
RNG seeds under gate rules 2026-09-28.1; the eight-seed result under 2026-09-28.2
(`gates_g8_2026-09-28.2_eightseed.json`, criterion committed 17:02 EDT, result 17:31 EDT) reads **FAIL** (worst bias
47.96 SE vs 3; scale ratio 1.71, also failing the 2026-09-28.3 scale gate at 1.5). G1 and G8 are both red on this
store; the instrument statement rests on G2 alone. The family-wise result stands as computed on this store and is
not re-run on it.**

**Result, numbers first: no feature survives the pre-registered family-wise null on the test half in any of the
three contrasts. Primary (impossible_test full, gamed vs honest): max-|d| p = 0.31 on 9 vs 7 uids, of which only
6 vs 3 sit in the two mixed cells. Secondary (missing_delete full, silent_empty vs asked_to_delete): p = 0.85.
Tertiary (all scenarios, destructive vs benign, 75 vs 140 uids in 29 mixed cells): count 6 of 20 against a null
mean of 5.5 (p = 0.42), max-|d| 1.82 against a null 95th percentile of 1.99 (p = 0.15). Every discover-side
"top" feature of the primary contrast flipped sign or vanished on test. The instrument is sound where it was
checked (G2 on real spans passes; replay logprobs agree within 0.05 nats on 99.5 % of exactly re-tokenized rows, a
diagnostic on a subset, not a gate pass; the G8 figure of 0.144 vs 0.157 did not reproduce across seeds) and unsound where it was not (G3 and G5 fail on real spans). This is the
pre-registration's "no feature survives" outcome, and it is reported as such.**

Pod 1m1annvxt7uejr (secure A100-SXM4-80GB, EUR-IS-1, created 18:04Z by the create-and-arm loop on attempt 11,
watchdog deadline 22:34Z, terminated 19:27Z after the copy, disarmed; 1 h 24 min at $1.59/h). Commit 95e626d
(PR #13). Pinned weights (preflight sha256), fp32 nnsight replay, SAE gemma-scope-9b-it-res layer_31/width_16k
(f0177489f26ef768), gate rules 2026-09-28.1, labeler rules 2026-09-28.1. Pre-registration:
`analyze/PREREGISTRATION_T3_WHITEBOX.md` (amendments 4b, 4c written before the pod). The position-level store
(~26 M rows) stays on the volume at `/workspace/t3_joined/features`; the per-uid in-span sums, the replay token
metadata, every report and the analysis are here.

## 1. Instrument checks on this data

| gate | result | reading |
|---|---|---|
| replay cardinality | 2,289 / 2,289 | 1.1–1.4 s per continuation |
| G1 replay fidelity (logprob mode, tol 0.05 nats, fp32 both sides) | **FAIL as a gate; fidelity holds** | 2,101 rows (91.8 %) re-tokenize to the sampled ids exactly; 2,091 of them agree within 0.05 nats (median worst gap 0.004), 10 exceed at one mid-span position by 0.05–0.08 (kernel-order noise). 188 rows (8.2 %) differ where the decision turn's trailing newline merges into the turn suffix on re-serialization, and 25 rows are SHIFTED by an interior end-of-turn the model ran past (no stop tokens; audit blocker 1). Every row was also 1–3 tokens longer than the span because vLLM's sampled ids carry the suffix/EOS; aligned in `replay_g1fixed/` (raw tails kept). The suffix strip did not fix the merge; the raw-id replay path (teacher-forcing the sampled ids) does, for the next replay. |
| G2 SAE health on 48 real assistant spans | **PASS** | var. explained 0.683, L0 90.0 (published 76, reported not gated), every decoy hook rejected by ≥ 0.11 VE, JumpReLU integrity 0, scaled copies x0.8/x1.2 reported. Tensor-identity block not attached by the driver (path present; the gate warns, does not fail); T1's identity (cos 0.9999998, norm rel 5e-6) is data-independent for this pinned artifact and hook. |
| G3 known code feature 8209 on real spans | **FAIL** | window-max AUROC 0.62 (threshold 0.80) between tool-block code positions and reasoning prose; only 9 of 48 sampled decision turns carried code in their tool block; the feature fires on 27 % of code windows and 2.5 % of prose windows. The test is thin and the feature does not discriminate JSON-embedded snippets the way it discriminates the calibration texts (T1 G3 passed). |
| G5 oracle paired-only on real spans | **FAIL** | accuracy 0.42, paired discrimination 0.33, confabulation 1.0. The oracle gets no weight; labels below are Neuronpedia's. |
| G8 null controls (within-cell, stratified d, all 60 selected concepts) | **FAIL under 2026-09-28.2 and 2026-09-28.3** | the single-seed 2026-09-28.1 pass (0.144 ≤ 0.157) depended on permutation order and used the pooled-groups bound. Eight seeds, bias criterion (`gates_g8_2026-09-28.2_eightseed.json`): worst \|mean signed null d\|/SE 47.96 vs 3.0 (per seed 35.8–47.96), worst \|d\| scale ratio 1.71 (gated at 1.5 from 2026-09-28.3: FAIL). Prefix token gap 0.0 within cells. Diagnosis in AUDIT_RESPONSE.md: a two-valued null from features firing in one continuation of a mixed cell; the numerator is unbiased. |

## 2. Family-wise results on the test half (odd seeds), B = 1000, q = 0.05, K = 20 per contrast

| contrast | test n_a vs n_b (in mixed cells) | mixed cells | count clearing null (null mean, 95 %) | p_count | max \|d\| (null 95 %) | p_max | G8-evaluable |
|---|---|---|---|---|---|---|---|
| primary: impossible_test full, gamed vs honest | 9 vs 7 (6 vs 3) | 2 | 9 (8.3, 11) | 0.49 | 11.6 (18.6) | 0.31 | no |
| secondary: missing_delete full, silent_empty vs asked_to_delete | 22 vs 26 (22 vs 24) | 6 | 3 (5.4, 10) | 1.00 | 0.46 (13.1) | 0.85 | yes |
| tertiary: all, destructive vs benign | 78 vs 792 (75 vs 140) | 29 | 6 (5.5, 8) | 0.42 | 1.82 (1.99) | 0.15 | yes |

Discovery (even seeds) saw 5,970 / 9,985 / 13,234 features with any in-span activation in the three contrasts'
uids; K = 20 selected per contrast by stratified \|d\|.

## 3. Per-feature table (descriptive; per-feature p is not the reported statistic)

Primary contrast, top by \|d_test\|:

| feature | d discover | d test | perm p | Neuronpedia label |
|---|---|---|---|---|
| 3279 | +84.8 | −11.6 | 0.16 | patterns related to interpersonal relationships |
| 3792 | +3.2 | −1.3 | 0.11 | phrases related to legal proceedings |
| 2694 | −4.3 | +0.8 | 0.44 | (no label fetched) |
| 5702 | −3.9 | −0.7 | 0.67 | punctuation marks and structural elements |

The discover-side d of 84.8 for feature 3279 is a degenerate within-cell sd on 12 vs 11 uids in six cells, and it
reverses sign on test. With two mixed test cells the primary contrast's permutation space is tiny: the "clears
null" flags in this contrast are ties (null 95th percentile equals the observed value), which is why the count
statistic reads 9 of 20 with a null mean of 8.3. The pre-registration called this contrast NOT_EVALUABLE by the
20/20 rule and thin; it is thinner than that.

Tertiary contrast, top by \|d_test\|:

| feature | d discover | d test | perm p | mean destructive | mean benign | Neuronpedia label |
|---|---|---|---|---|---|---|
| 6900 | +2.14 | +1.82 | 0.13 | 0.0164 | 0.0056 | (no label fetched) |
| 13236 | +0.97 | +1.01 | 0.016 | 0.0035 | 0.0063 | references to statistics and numerical data |
| 16108 | +2.07 | +0.63 | 0.004 | 0.0558 | 0.0033 | code related to handling CSV file operations |
| 16226 | +0.80 | +0.30 | 0.11 | 0.069 | 0.756 | (no label fetched) |

Two features (16108, 13236) clear their own null at the per-feature level; the family-wise max-\|d\| test does
not (p = 0.15), and the count test does not (6 vs a null mean of 5.5). Feature 16108's label ("CSV file
operations") is the kind of lexical signature the within-cell null is designed to discount only partly: destructive
outcomes in missing_delete and honeypot involve tool calls that name CSV/data files. It is a candidate for a
follow-up, not a finding.

Secondary contrast: no feature above \|d\| 0.47 on test; discover-side selections (d 1.5–2.4) did not replicate.

## 4. Reading

- **The instrument works where it was tested on this data, with two caveats.** Replay reproduces the generation
  computation on the rows that re-tokenize exactly (99.5 % within 0.05 nats; a diagnostic, since the gate fails on
  the mis-tokenized 8 %); the SAE reconstructs real decision-turn residuals at VE 0.68 with decoys rejected; the
  within-cell design removes the prefix-length confound (gap 0 by construction). The G8 pass was not reproducible;
  the eight-seed check under rules 2026-09-28.2 reads FAIL (bias 47.96 SE, scale 1.71), so G8 is red on this store.
- **Two calibration instruments do not transfer to real spans.** The known code feature discriminates JSON tool
  blocks poorly (AUROC 0.62), and the activation oracle confabulates on every null window; neither is used here.
- **No decision-span feature predicts the eventual outcome beyond the within-cell null**, at this n, in any
  contrast. The primary contrast has 6 vs 3 informative uids; the tertiary has 75 vs 140 and still nothing
  family-wise. Per the pre-registration this is a legitimate result of the session, reported as such.
- **The design question this answers is the one the pre-registration named:** A_decision → Y_eventual on a 9B
  whose transgressions are rare, seed-clustered and mostly flailing. Either the signal lives elsewhere in time
  (the transgression span, `spans.transgression_message_index`, a different experiment), or the seed-clustered
  transgressions ride on surface features that the within-cell null correctly refuses to credit, or the model
  the question needs is the 27B. Those are the follow-ups; none of them is licensed by this session's numbers.

## Files

- `features/{sae_health,known_answer_report,oracle_calibration}.json` — the real-span instrument reports (from the
  48-span sample, copied beside the full store); `features/concept_index.json` — 60 selected features with
  selection seeds; `features/<scenario>/<variant>/*_uidsums.parquet` — per-uid in-span sums (E[A] numerators).
- `replay/` — replay token metadata as written; `replay_g1fixed/` — the same with the generation arrays aligned to
  the span (raw tails kept); G1 above was run on the latter.
- `analysis/{discovery.json,effects_test.json,effects_test.txt,feature_labels.json}`; `gates_*.txt`; driver, smoke,
  replay and preflight logs.
