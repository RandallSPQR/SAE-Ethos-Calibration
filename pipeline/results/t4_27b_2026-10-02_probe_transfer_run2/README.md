# Probe-regime transfer, run 2 (2026-10-03): the valid run of queue item 5

Gate rules 2026-10-02.1 (pre-registered before run 1; unchanged). Code eb88afc: agent cap 1024 + truncation STOP, the fix
for run 1 (`../t4_27b_2026-10-02_probe_transfer_run1_truncated/`, INVALID). Pod `ck8va0ptvoyaae`, 2 x A100 SXM 80 GB,
EUR-IS-1, 15:07:22 to 16:06:26 UTC (59 min), about $3.13; terminated by API after copy-back. Item 5 total about $5.47.
The offline re-score (`python -m probe.transfer --run-dir <this dir>`) reproduces the pod's table.

## Instrument checks

| regime / task | trials | dropped | truncated (cap) | reply tokens median / max | switching point |
|---|---|---|---|---|---|
| native / lottery | 1,120 | 0 | 0 (16) | 4 / 5 | 88.5 at safe = 50 |
| native / ultimatum | 248 | 0 | 0 (16) | 3 / 4 | 5.5 |
| agent / lottery | 1,120 | 47 (4.2 %) | 0 (1024) | 102 / 207 | 100.2 at safe = 50 |
| agent / ultimatum | 248 | 13 (5.2 %) | 0 (1024) | 138 / 207 | 17.6 |

Every agent drop is an ask_user call (a dropped trial by the pre-registered parser), not a truncation. They sit near hard
decisions (lottery: low n at safe = 100 and high n at safe = 30; ultimatum: low offers).

## Verdicts (cleaned direction decides use)

| task | layer | native held-out AUROC | agent AUROC clean [95 %] | verdict | agent AUROC raw [95 %] | raw |
|---|---|---|---|---|---|---|
| lottery | 30 | 0.926 | 0.865 [0.826, 0.898] | **PASS** | 0.876 [0.832, 0.913] | PASS |
| lottery | 38 | 0.992 | 0.839 [0.803, 0.871] | **PASS** | 0.859 [0.820, 0.891] | PASS |
| lottery | 40 | 0.992 | 0.604 [0.553, 0.653] | **FAIL** | 0.750 [0.699, 0.798] | PASS |
| lottery | 46 | 0.993 | 0.672 [0.620, 0.722] | **FAIL** | 0.600 [0.551, 0.650] | FAIL |
| ultimatum | 30 | - | 0.434 [0.246, 0.629] | **FAIL** | 0.422 [0.235, 0.626] | FAIL |
| ultimatum | 38 | - | 0.818 [0.687, 0.906] | **PASS** | 0.818 [0.685, 0.907] | PASS |
| ultimatum | 40 | - | 0.866 [0.763, 0.932] | **PASS** | 0.865 [0.762, 0.931] | PASS |
| ultimatum | 46 | - | 0.841 [0.729, 0.916] | **PASS** | 0.842 [0.731, 0.917] | PASS |

Usable (cleaned PASS): lottery layers 30, 38; ultimatum layers 38, 40, 46. By the rule, the lottery probe is not used at
layers 40 and 46, and the ultimatum probe is not used at layer 30.

## Notes for reading
- Layer 40 (the primary SAE layer): ultimatum PASS; lottery FAIL on the cleaned direction (0.604) although the raw one
  passes (0.750). The surface-cleaning step removes most of what transfers at L40 for the lottery.
- In-regime fit is not transfer: lottery native held-out AUROC is 0.99 at layers 38 to 46, yet layers 40 and 46 fail in
  the agent frame.
- The frame moves the behavior itself: switching points shift 88.5 -> 100.2 (lottery, safe = 50) and 5.5 -> 17.6
  (ultimatum). The ultimatum probe is trained on 20 native rejections, all at offers 0 to 6, and tested on 41 agent
  rejections spread over a wider range; it has no native held-out level (ultimatum has one level), so its native
  reference column is blank.
- Agent class balance: lottery 0.35 risky, ultimatum 0.83 accept.

`activations.npz` files are gitignored and kept locally.
