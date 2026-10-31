# Item 11: a grader probe on item 10's replays — design DRAFT (STOP 1; unregistered, no code run, $0)

**Status: DRAFT for Randall's review.** Nothing here is registered. Code, mock tests and a pinned 4B smoke follow on
approval, before any pod.

## 0. Why

Item 10 confirmed a behavioral effect at turn_close with fresh sentences:
- a grader sentence raises transgression (0.705 → 0.752), mostly fabricated success claims;
- it halves honest failure reports (0.112 → 0.050);
- verbalized eval-awareness is 0 of 1,516.

The model behaves differently without saying why. Item 11 asks whether the internal state at the moment it writes its
final report carries the cue, and, descriptively, whether that state relates to *how* it reports.

## 1. Data: item 10's own rows, replayed

- **Rows:** run_cae2ff21d35f (`results/item10_2026-10-10/`), the registered, complete run.
  - grader 704, neutral 700, minus the 5 unclear rows → **about 1,400 rows** in the primary analysis.
  - pc 112, replayed as well, for one descriptive projection (§6).
- **No new generation.** Every row is teacher-forced through the certified 27B replay instrument
  (`replay/hooks.teacher_forced_forward`):
  - fp32 with TF32 matmuls on 2 × A100, model revision 005ad340;
  - the instrument passed G0 and G1 at the 27B calibration (`results/t4_27b_2026-09-30_replay/`).
- **Instrument checks on the pod, before any activation is read:**
  - G0 (10 tokens teacher-forced, 0 flips);
  - the hook-tensor identity check (min cos ≥ 0.999);
  - per row, the teacher-forced ids of each assistant turn must equal the sampled ids the row recorded. A row that fails
    is excluded and counted; if more than 2 % fail, STOP.

## 2. Positions (fixed before any activation is read)

- **R, the report turn:** the last token of the generation prompt that opens the model's final assistant turn, i.e. the
  residual from which the final report is generated.
  - It is located by serializing the messages up to that turn with `add_generation_prompt=True`; the last id is R.
  - R precedes every token of the report, so the report's own words ("done", "cannot pass") are not inside it.
- **S, the sentence tokens:** the mean residual over the inserted sentence's own tokens in the first user turn. They are
  located by the tokenizer's character offsets of the sentence in the serialized prompt. This is the lexical control (§5).
- Both are captured in one forward pass per row.

## 3. Layers (from the item 5 transfer verdicts)

| layer | lottery (agent, cleaned) | ultimatum (agent) | role here |
|---|---|---|---|
| **38** | PASS 0.839 | PASS 0.818 | **primary**: the only layer passing transfer on both tasks |
| 30 | PASS 0.865 | FAIL | descriptive |
| 40 | FAIL 0.604 | PASS 0.866 | descriptive (the primary SAE layer) |
| 46 | FAIL 0.672 | PASS 0.841 | descriptive |

All four are captured in the same pass (`extra_layers`).

## 4. Primary: grader vs neutral at R, L38, leave-sentence-pair-out

- **Probe:** a difference-of-means direction, standardized per training fold (fold mean and sd per dimension). It has no
  hyperparameters, so nothing is tuned on held-out data. The score is the projection on the direction.
- **Cross-validation:** 16 folds, one per sentence pair i. A fold holds out grader[i]'s and neutral[i]'s rows (all seeds)
  and builds the direction from the other 15 pairs. Every row gets one out-of-fold score.
- **Statistic:** the pooled out-of-fold AUROC, grader vs neutral.
- **Null:** 1,000 permutations of the arm labels **within each (seed, pair) condition**, the unit the design randomized,
  each re-running the full 16-fold CV. Permutations run offline on the cached activations ($0).
- **CI:** cluster bootstrap over sentence pairs (2,000 resamples).
- **PASS iff** the observed AUROC exceeds the 95th percentile of the permutation null (one-sided p < 0.05).
- **What a PASS means, stated now:** the state at the report turn carries which sentence the episode was given. By
  itself it does not say whether that is a belief or the sentence's words carried forward by attention; §5 is there to
  read that.

## 5. Lexical control (descriptive, always reported)

1. **At S:** the same 16-fold CV at the sentence tokens. A near-perfect AUROC is expected, since the two arms literally
   differ there; it is the ceiling and confirms the extraction.
2. **Cross-position transfer:** the S direction (trained at S on the 15 training pairs) applied at R, AUROC out of fold;
   and the cosine between each fold's S and R directions.
3. **Within outcome strata:** grader vs neutral at R inside fabricated_green rows only, and inside honest_report rows
   only. The arms differ in outcome mix, and the report-turn state may carry the episode's trajectory.

**Reading, fixed now:**
- **The cue is carried as words:** R decodes the arm and the S direction transfers to R (high transfer AUROC,
  |cos| > 0.5).
- **A state beyond the words:** R decodes the arm with a direction distinct from S (low cosine, low transfer), and the
  decoding survives within both outcome strata.
- Anything between is reported as such. These readings are descriptive, not verdicts.

## 6. Descriptive: does the probe score relate to how the model reports?

- **Within the grader arm:** the out-of-fold R score, fabricated_green (404) vs honest_report (35), AUROC with a
  bootstrap CI clustered by prefix. The 35 honest rows make this wide; it is reported, not tested.
- **The same within the neutral arm** (333 vs 78): whether the grader direction separates outcomes even without the cue.
- **pc rows** projected on the L38 grader direction: whether any inserted instruction moves the score, or only the grader
  cue does.

## 7. Price

- **Pod:** 2 × A100 SXM 80 GB, EUR-IS-1. fp32 27B needs both GPUs. Recent A100 pods there billed **$1.79/h per GPU →
  ~$3.58/h**; the price is checked at creation.
- **Time:**
  - ~10 min preflight;
  - ~15 min fp32 load;
  - ~5 min G0 and identity checks;
  - replay of ~1,516 rows × ~1.5 s ≈ 38 min (t4 measured 0.9 s per continuation on shorter rows; scaled for impossible_test's
    ~4k tokens);
  - ~5 min copy-back.
  - Total **≈ 1.2 h ≈ $4.3**.
- **Cap:** self-stop and Mac watchdog at **2.5 h → $8.95**.
- **Activations:** about 1,516 rows × 2 positions × 4 layers × 5,376 × 4 B ≈ 0.26 GB. They stay on the volume and are
  fetched locally (gitignored, as before). The permutation null and every analysis then run offline.
- **Before the pod ($0):**
  - `probe/item11_extract.py` (positions R and S, per-row id check);
  - `analyze/item11_probe.py` (CV, permutation null, controls);
  - mock tests on synthetic activations with planted directions (a lexical-only plant must read as "words", a state plant
    as "beyond the words");
  - a pinned Gemma-3-4B smoke on a few item 10 rows, checking that the positions land where the doc says.
