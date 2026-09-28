# Pre-registration — T3 white-box analysis (written 2026-09-28, before the replay pod bills)

Data: the joined T3 set (`results/t3_2026-09-28_joined`, run_febbc90c06ea + run_64df1ae8a9a0, 2,289
continuations, labeler rules 2026-09-28.1, G6 κ 0.908). Nothing below is changed after the pod returns; a
deviation is written as a deviation.

1. **Split.** Feature discovery on even seeds (discover). Effects and G8's null on odd seeds (test). Behavioral
   rates on both (done). `analyze/split.py` is the only definition of the split.

2. **Contrasts, decision-span activations, episode-outcome label (A_decision → Y_eventual).** The feature
   store scores `scored_message_index` (the first assistant turn of the continuation); the label is the
   episode outcome. Primary: impossible_test / full, **gamed** = {edited_test, fabricated_green,
   edited_test_concealed, subverted_harness} (pooled) vs **honest** = {honest_report, asked}. Secondary:
   missing_delete_tool / full, **silent_empty** vs **asked_to_delete**. Tertiary (exploratory, more n):
   all scenarios and variants pooled, destructive taxonomies {deception_concealment, destructive,
   task_cheating, safeguard_bypass, overeager} vs benign. Contrast definitions live in `analyze/effects.py:CONTRASTS`.

3. **Null.** G8 permutes labels within (scenario, variant, seed) cells, never across cells. Seeds 18 and 19
   carry 9 of impossible_test's 23 full transgressions; a feature that reads "seed 18's rendered surface"
   must not pass as a transgression feature. The same within-cell permutation is the null for every effect
   below.

4. **Statistic, family-wise.** Per feature: E[A] per continuation = activation summed over the assistant
   span of the scored turn / assistant token count (zeros included, every labeled uid included). Discovery
   selects the K = 20 features with the largest |Cohen's d| between the contrast groups on discover seeds
   (per contrast). On test seeds, each selected feature gets its d and a within-cell permutation p (B = 1000,
   descriptive only). **The reported number is the count of selected features whose |d| exceeds their own
   null 95th percentile (q = 0.05), against the null distribution of that count** (the same B permutations
   applied to all K features at once); the family-wise p is P(null count ≥ observed count). Not 16k
   per-feature p-values.
   **4b (amendment, 2026-09-28, written after the mock dry run and before the pod billed):** the count
   statistic cannot see one strong feature (its null expectation is K·q = 1), so the max-|d| statistic over
   the K selected features (Westfall–Young) is reported beside it with its own permutation p and the number
   of features whose |d| exceeds the max-null (1−q) quantile. Both are pre-specified; neither is chosen after
   the fact. The mock dry run (planted feature 7, d ≈ 11) produced count = 1, p_family = 0.68, which is the
   weakness this amendment addresses.
   **4c (amendment, 2026-09-28, before the pod):** a within-cell null needs a within-cell statistic. The
   per-feature effect is the **stratified Cohen's d**: over (scenario, variant, seed) cells holding both groups,
   the size-weighted mean of the within-cell mean differences divided by the pooled within-cell sd. The pooled
   d is not exchangeable under within-cell permutation (on the T3 mock a purely label-planted feature kept
   d ≈ 1.3 under the "null" because 83 of 114 test cells are label-homogeneous). Discovery, effects, and G8 all
   use the stratified d; G8's minimum-group rule counts uids in mixed cells only. Cells with one label carry
   no within-cell information and contribute nothing; the between-cell component of any effect is, by
   design, not credited.

5. **Power and the honest outcome.** Roughly 11 gamed episodes on discover seeds and 12 on test seeds in
   the primary contrast; G8's 20/20 minimum-group rule (2026-09-24.1) therefore makes the primary contrast's
   G8 **NOT_EVALUABLE by construction**, and it is reported as exploratory with that status. "No feature
   survives the null" is a legitimate result of this session and is reported as such. The tertiary contrast
   has ~45 destructive uids on test seeds and is the one G8 can evaluate.

**Instrument checks first, on this data:** G1 at 100 % replay cardinality on T3's transcripts (logprob mode,
T = 0.8, tolerance 0.05 nats, fp32 both sides); G2 (SAE health, decoy hooks, JumpReLU integrity) recomputed
on real T3 assistant spans, with the tensor-identity block carried from T1 (same pinned artifact and hook;
the identity check is data-independent and is labeled as carried); G3 (known code feature 8209) on real
spans: tool-block code positions vs reasoning-prose positions, window-max AUROC; G5 oracle paired-only on
real-span residuals captured before the LoRA loads. Then Neuronpedia labels for whatever survives.

Order of reporting when the pod returns: cardinality and gate readouts, then the family-wise counts, then
the per-feature table, then the labels. Numbers first, story second.


## Addendum: deep resample of the responsive cells (written 2026-09-28, before the pod)

The T3 white-box session found no feature surviving the within-cell null, with the primary contrast holding 6 vs
3 informative uids. Before closing the 9B study, the within-cell question is asked once at proper power:

- **Cells**: MIXED full cells only (a cell that is 10/10 has no within-cell contrast). impossible_test full seeds
  16 and 18 (even: discovery) and 19 and 3 (odd: test); missing_delete_tool full seeds 14 and 6 (even) and 13 and 5
  (odd). Same rendered surfaces, same decision points (the greedy prefix is deterministic), n = 100 continuations
  per cell, same temperature, same nudge, same commit family as T3.
- **Contrasts and statistics**: unchanged (primary and secondary as in §2; stratified within-cell d; discovery K = 20
  on the even cells; count and max-|d| family-wise on the odd cells, B = 1000, q = 0.05; G8 within-cell). Expected
  informative n on test: impossible_test ~ 60 gamed vs 60 honest across two cells; missing_delete ~ 120 silent vs 60
  asked across two cells (T3 per-cell rates).
- **Instrument checks**: G1 on 100 % of the replayed rows (span-aligned); G2/G3/G5 are not rerun (same artifact,
  same span distribution as the session that measured them).
- **Outcomes allowed in advance**: (a) features survive family-wise on the odd cells → the within-cell signal exists
  on the 9B and is reported with labels; (b) nothing survives at this power → the 9B study closes with "no
  decision-span predictor beyond the cell" as its white-box result. Either closes the 9B honestly.
- **Cost**: one pod, ~800 continuations (~3 h at concurrency 8) + replay (~25 min), ~$5.5, watchdog 5 h.
