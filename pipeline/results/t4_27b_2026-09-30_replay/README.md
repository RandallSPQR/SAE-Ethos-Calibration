# 27B replay ladder and G1 calibration, 2026-09-30 (pod mv51cqg1q1bap2)

Gemma-3-27B-IT replayed **fp32 with TF32 matmuls** on **2 x A100 SXM 80 GB** (EUR-IS-1), loaded as nnsight
`VisionLanguageModel` split over both cards (`device_map: auto`), SAEs Gemma Scope 2 `resid_post` layer 40 (primary) and
layer 53 (pre-registered secondary), 16k, L0 medium. Code 682c855 (ladder + calibration). Created 02:19:35Z with the
pod-side self-stop in its start command, self-terminated 03:29:26Z: **1.16 h, $3.70**.

The first launch (code 12a8e41) stopped at model load: nnsight 0.7 refuses `LanguageModel` for Gemma-3 (registered as
image-text-to-text); the profile now names the load class (682c855). The G4 step then crashed because no anchor feature
is chosen for this SAE (G3 had computed over every feature): fixed after the run in ce478c1 (G3/G4 skipped, G3
NOT_EVALUABLE). The two 52 MB G3 reports that bug produced were replaced by stubs recording their hashes.

## Ladder (gate rules 2026-09-30.1)

| gate | layer 40 (primary) | layer 53 (secondary) |
|---|---|---|
| G0 (teacher-forced under the dtype split) | **PASS**: 10 tokens, 0 flips; nnsight greedy "11, 13, 17" = vLLM's | (same model path) |
| G1 fixture (T=0, exact mode) | **PASS**: 95/96 ids, 1 flip excused (near-tie at k=87) | (same) |
| G2 SAE health: VE / L0 (published 60) | 0.835 / 60.9 (matches) | 0.727 / 69.3 (just outside the 15 % band: reported, not gated) |
| G2 JumpReLU integrity | exact | exact |
| G2 scale copies x0.8 / x1.2 (VE) | 0.782 / 0.790 (below the chosen: VE sees scale here) | 0.672 / 0.663 |
| G2 decoys mlp / attn / post-norm (VE) | -4e8 / -1e8 / -3658 | -1e9 / -2e9 / -8802 |
| **G2 decoy: block input (resid_pre), VE** | **0.836 (margin -0.001)** | **0.723 (margin 0.004)** |
| tensor identity vs transformers hidden states (fp32) | min cos **0.9999954**, max norm rel 0.0022 | min cos **0.9999815**, max norm rel 0.0020 |
| identity cross-check: block output vs block input | median cos 0.9981, min 0.9901 | median 0.9989, min 0.9821 |
| **G2 verdict (2026-09-30.1)** | **FAIL** (decoy margin) | **FAIL** (decoy margin) |
| G3 | no anchor chosen (NOT_EVALUABLE after ce478c1; the FAIL in the log is the bug) | same |

**The G2 failure is construction, not the hook.** At 0.645 and 0.855 depth one block changes the residual stream by
about 7 % of its norm, inside the SAE's own reconstruction error (FVU 0.165 / 0.273), so variance explained cannot tell the
block's input from its output. Tensor identity can: the chosen tensor sits at cosine 0.999995 to the independent
reference, the block input at 0.990 minimum, against the 0.999 tolerance. A rule that lets identity reject a same-shape
decoy where VE cannot (proposed 2026-09-30.2, not applied, Randall's decision) would read PASS on both layers; until then
G2 reads FAIL under 2026-09-30.1 and nothing that depends on the SAE hook is cited.

## G1 mixed-dtype calibration (rules 2026-09-29.2, 2026-09-30.1): **VALID**

`27b/cal/g1_calibration.json`, sha256 `21119e19fc50c083f1a84c3a4ceff4d6f1856e08d085965349ee4af7854b1c00`, pinned in
run.yaml (`gates.g1_mixed`). Built from run_bb664336cb2d (355 rows; not analysed for behavior).

| item | value |
|---|---|
| identity | served bfloat16, replayed float32, TF32 on, replay card "2x NVIDIA A100-SXM4-80GB", model revision 005ad340 |
| rows | 355 (floor 300) |
| crosscheck: median gap served-vs-fp32 / HF-bf16-vs-fp32 | **0.83** (limit 2.0): vLLM adds nothing beyond dtype |
| derived tol_row / tol_bulk | **0.435** nats / **0.0106** nats |
| clean calibration under its own thresholds | passes |
| planted defects: off_by_one, boundary_shift, sparse_1pct, template_prefix (72-row GPU replay) | **all FAIL** |

Other checks on this pod: fp32 replay 355/355 at 0.9 s per continuation (both SAE layers in one pass); the same-pass
layer-53 capture equals a dedicated layer-53 replay on 24 rows, worst relative difference **0.0**.
