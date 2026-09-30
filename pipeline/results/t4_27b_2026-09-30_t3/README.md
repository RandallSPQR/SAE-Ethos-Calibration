# 27B T3 generation, 2026-09-30 (pod g70iafrac4xyr7, run_df0365347b16)

Pre-registered in `analyze/PREREGISTRATION_T3_27B.md` (committed 3ac7cc8, before this pod). Gemma-3-27B-IT served bf16
(vLLM 0.11.0), profile pinned, code 3ac7cc8, `calibrate/run_serve.sh t3` in two bursts on one A100 SXM (EUR-IS-1):
seeds 0-9 (05:36-07:43Z, 1,200 continuations in 2.07 h), then seeds 10-19 (07:45Z on; both stops re-armed to 11:45Z at the
boundary). Pod created 05:24:42Z with the self-stop in its start command, self-terminated 10:11:40Z: **4.78 h, $7.60**.

**Rates are embargoed** (pre-registration: until a fresh blind G6 fifty from this run certifies labeler 2026-09-30.2).
This README carries cardinality and instrument readings only; `gates_g7.txt` was written by the driver and is not quoted.

## Cardinality

| item | value |
|---|---|
| continuations | **2,390** of 2,400 planned, one run_id, two harness invocations merged |
| excluded prefixes (counted) | 1: `missing_delete_tool/seed_010/full` (no tool call before the obstacle; step 13) |
| excluded continuations | 0 |
| overran_turn | 0 |
| served dtype on every row | bfloat16 |
| continuation budget | `fixed` on every row (harness 2026-09-30.2; e.g. credential start_step 4, budget 16) |

## Instrument readings

- Isolation: seccomp_uid 10/10 canaries at both bursts (probe) and at every harness launch.
- Preflight: weights and both SAEs sha256-verified; tokenizer facts and template agreement checked at each burst.
- Verifiable push (harness 2026-09-30.1): push calls print receipts and submits carry them (existence checked during
  burst 1; no rate read).

## Next

Replay of every row on the replay pod (2 x A100, fp32 + TF32): G1 under the pinned calibration (sha 21119e19), G2 on real
spans (rules 2026-09-30.2), G3 two-anchor (2026-09-30.3), both SAE layers. Then the G6 fifty.
