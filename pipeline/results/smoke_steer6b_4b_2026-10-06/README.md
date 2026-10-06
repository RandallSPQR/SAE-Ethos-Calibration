# Item 6b pod code paths on Gemma-3-4B-IT (2026-10-06, local CPU, bf16, under the 27B replay pins, $0): 9/9 PASS

`python -m probe.smoke_steer6b_4b` (command in the module docstring; transformers 5.17.0, nnsight 0.7.0, accelerate 1.15.0,
torch 2.8.0: the versions on the pod's replay venv). Mechanics only, at 4B layer 22; nothing is analysed.

- The replay pins check passes; "A" / "B" are single distinct tokens (236776 / 236799 in the Gemma-3 tokenizer).
- The A/B exact readout works at lambda 0 (A/B mass 1.0 on all six evaluation prompts), and in sampled A/B answers the first
  token decides the parsed letter (4/4: "B", "B", "A", "A").
- Absolute steering mode: the HF-hook path equals the nnsight path exactly at 0 and +-4000 (gap 0.0), and +-4000 moves the
  top-20 log-probs by up to 34 nats.
- Both CAA builds run (A/B and word: unit vectors, token-direction cosines and removed shares logged, sha256); the
  embedding and unembedding difference directions are identical (cos 1.000: tied embeddings).
- The manipulation prompt generates and parses: 3/3 correct on the 4B ("The guaranteed amount for the Safe Option is 70
  tokens. The Risky Option has a 50% chance ...").
A first run failed check 5 because its strength (+-200, 0.5 % of the 4B's ~40k residual norm) on saturated items moved
nothing; the check was strengthened to +-4000 and judged on log-probs.

Record (Randall, 2026-10-06): smoke check 5 strength raised after the first failure; code under test unchanged.
