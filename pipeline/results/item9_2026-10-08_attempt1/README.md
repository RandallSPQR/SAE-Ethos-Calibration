# Item 9, attempt 1 (2026-10-08): STOP, DISCARDED

Pod ia0fzssr00ce6o (1 × A100, EUR-IS-1, **$1.79/h**), 18:18:17 to 19:06:01 UTC (0.80 h ≈ **$1.43**); terminated, 0 pods.
Code 29a2223 (rules 2026-10-08.2).

**STOP** in block 0: `lane 1: FileNotFoundError: [Errno 2] No such file or directory: '/tmp/arm_a_3mzkzdq3'`. Cause: the two
lanes used the same episode uids (sandbox slot = continuation index only), and sandbox cleanup reaps and sweeps per uid,
so each lane's cleanup could kill the other's processes and delete its sandbox tree. Fixed in harness change 2026-10-08.4
(`run_scenario_seed(slot_base=...)`, lane k at 16k).

**Discarded whole.** The 68 rows written before the STOP may carry outcomes damaged by the other lane's reaps; they enter
no analysis. `27b/item9/analysis/` is the pod's automatic analysis of those rows, kept for the record only.

Also recorded: the pod billed $1.79/h, not the $1.59/h quoted at STOP 1; both stops were moved at creation to 01:24 UTC
(7.1 h) to hold the $12.72 cap.
