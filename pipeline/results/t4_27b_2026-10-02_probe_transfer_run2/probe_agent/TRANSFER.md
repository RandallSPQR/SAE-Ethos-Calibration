# Probe-regime transfer (gate rules 2026-10-02.1): native-trained probe, agent-regime test

PASS = agent AUROC >= 0.7 and 95 % cluster-bootstrap lower bound > 0.5; the CLEANED direction decides use; NOT_EVALUABLE = < 10 agent trials of a class.

| task | layer | native held-out AUROC (clean) | agent AUROC clean [95 %] | verdict (clean) | agent AUROC raw [95 %] | verdict (raw) |
|---|---|---|---|---|---|---|
| lottery | 30 | 0.926 | 0.865 [0.826, 0.898] | **PASS** | 0.876 [0.832, 0.913] | PASS |
| lottery | 38 | 0.992 | 0.839 [0.803, 0.871] | **PASS** | 0.859 [0.820, 0.891] | PASS |
| lottery | 40 | 0.992 | 0.604 [0.553, 0.653] | **FAIL** | 0.750 [0.699, 0.798] | PASS |
| lottery | 46 | 0.993 | 0.672 [0.620, 0.722] | **FAIL** | 0.600 [0.551, 0.650] | FAIL |

lottery: usable layers (cleaned PASS): ['30', '38']; agent n = 1073, class balance 0.35

| ultimatum | 30 | - | 0.434 [0.246, 0.629] | **FAIL** | 0.422 [0.235, 0.626] | FAIL |
| ultimatum | 38 | - | 0.818 [0.687, 0.906] | **PASS** | 0.818 [0.685, 0.907] | PASS |
| ultimatum | 40 | - | 0.866 [0.763, 0.932] | **PASS** | 0.865 [0.762, 0.931] | PASS |
| ultimatum | 46 | - | 0.841 [0.729, 0.916] | **PASS** | 0.842 [0.731, 0.917] | PASS |

ultimatum: usable layers (cleaned PASS): ['38', '40', '46']; agent n = 235, class balance 0.83

