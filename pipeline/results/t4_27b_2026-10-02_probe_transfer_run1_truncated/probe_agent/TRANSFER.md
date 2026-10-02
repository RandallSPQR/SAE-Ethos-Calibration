# Probe-regime transfer (gate rules 2026-10-02.1): native-trained probe, agent-regime test

PASS = agent AUROC >= 0.7 and 95 % cluster-bootstrap lower bound > 0.5; the CLEANED direction decides use; NOT_EVALUABLE = < 10 agent trials of a class.

| task | layer | native held-out AUROC (clean) | agent AUROC clean [95 %] | verdict (clean) | agent AUROC raw [95 %] | verdict (raw) |
|---|---|---|---|---|---|---|
| lottery | 30 | 0.926 | 0.879 [0.823, 0.924] | **PASS** | 0.895 [0.827, 0.947] | PASS |
| lottery | 38 | 0.991 | 0.881 [0.816, 0.933] | **PASS** | 0.887 [0.816, 0.942] | PASS |
| lottery | 40 | 0.994 | 0.786 [0.719, 0.840] | **PASS** | 0.834 [0.759, 0.894] | PASS |
| lottery | 46 | 0.995 | 0.758 [0.692, 0.817] | **PASS** | 0.708 [0.637, 0.771] | PASS |

lottery: usable layers (cleaned PASS): ['30', '38', '40', '46']; agent n = 450, class balance 0.16

| ultimatum | 30 | - | - [-, -] | **NOT_EVALUABLE** | - [-, -] | NOT_EVALUABLE |
| ultimatum | 38 | - | - [-, -] | **NOT_EVALUABLE** | - [-, -] | NOT_EVALUABLE |
| ultimatum | 40 | - | - [-, -] | **NOT_EVALUABLE** | - [-, -] | NOT_EVALUABLE |
| ultimatum | 46 | - | - [-, -] | **NOT_EVALUABLE** | - [-, -] | NOT_EVALUABLE |

ultimatum: usable layers (cleaned PASS): none; agent n = 23, class balance 1.00

