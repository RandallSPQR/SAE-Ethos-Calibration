# T3 session 2 (test half + missing_delete even-seed redo), 2026-09-28 — run_64df1ae8a9a0

**Status: generated. All rates are EMBARGOED until G6 passes on the third fifty (drawn from session 1's discover
half). This README records provenance and cardinality only.**

Identity: commit `3fc27d4` (main; code identical to d43c073, which fixes the empty-`find` / repo-escape harness bug
of session 1; code_hash f635a3bee7ccbcda), pinned run (`--require-pinned` passed; only `sae.published.fvu`
unverified). Secure A100-SXM4-80GB in EUR-IS-1 (pod ljm8g10pwzwqbz, created 23:40Z 2026-09-27 by the
create-and-arm loop on the first attempt, watchdog deadline 07:10Z, terminated 05:04Z after the copy, disarmed;
5 h 24 min at $1.59/h). Confinement seccomp_uid + Landlock ABI 4, 10/10 canaries. Labeler rules 2026-09-28.1,
gate rules 2026-09-24.1.

## What ran

Two driver stages into the same run directory (same commit and config, so the same content-derived run_id):

1. **Test half**: seeds 1, 3, ..., 19, all four scenarios × three variants × n = 10.
2. **Redo of missing_delete_tool's discover half**: seeds 0, 2, ..., 18, under the fixed harness. Session 1's
   `missing_delete_tool__seed*.jsonl` files (run_febbc90c06ea) are superseded by these and must be DROPPED from
   any join; they lost 7 continuations to a bug that hit exactly the emptying move in the full variant.

## Cardinality

| scenario | half | continuations |
|---|---|---|
| credential_obstacle | test (odd) | 300 |
| honeypot_forbidden_table | test (odd) | 300 |
| impossible_test | test (odd) | 270 (seed 13: no variant reached the trigger) |
| missing_delete_tool | test (odd) | 270 (seed 9 full; seeds 11 and 19 one variant each did not reach the trigger) |
| missing_delete_tool | discover redo (even) | 280 (seed 10 control and mention did not reach the trigger, reproducing session 1) |
| total | | 1,420 |

Excluded continuations (harness exceptions): **0** in both stages. `main_run/cardinality.json` holds the stage 2
tally only (the harness rewrites it per invocation); `cardinality_from_log.json` recovers stage 1's exclusions
from the driver log. Excluded prefixes are reach facts and stand.

## Joining the two sessions for analysis

- run_febbc90c06ea (session 1, `results/t3_2026-09-27_session1/main_run`): credential, honeypot, impossible_test,
  even seeds; DROP its missing_delete_tool files.
- run_64df1ae8a9a0 (this run): all four scenarios odd seeds, plus missing_delete_tool even seeds.
- Every uid appears once in the union; readers refuse duplicates, so the drop is explicit. Behavioral rates use all
  twenty seeds; discovery on even, G8's null on odd (`analyze/split.py`).

## Files

- `main_run/` — generation (50 seed files), manifest with isolation block, stage 2 cardinality. Immutable.
- `cardinality_from_log.json` — both stages' exclusions from the driver log.
- `logs/` — driver (both stages), harness, probe, preflight, pod-side gate readout (embargoed content; not summarized).
