# Item 6 steering backend smoke on Gemma-3-4B-IT (2026-10-03, local CPU, fp32, $0): 11/11 PASS

`python -m probe.smoke_steer_4b` (command in the module docstring). Mechanics only: a random unit vector at 4B layer 22;
nothing here is analysed. Same model class (Gemma3ForConditionalGeneration) and tokenizer as the 27B.

What it established for the pod:
- The option first-token ids under the Gemma-3 tokenizer are run 2's observed 27B ids (99510 / 39316).
- The HF-hook path (used for KV-cached generation) equals the nnsight path at prefill exactly (gap 0.0 at lambda 0, +-0.4),
  and the GPU-style torch readout equals the numpy readout of `steer_exact` (6.8e-21).
- Seeded per-row sampling is reproducible; under sampling, the first token decides the parsed answer (4/4).
- Batch gate in fp32: batched == unbatched (0.0 unsteered, 6.7e-5 steered; tol 0.05). A first run in bf16 showed 0.87
  nats: dtype noise between padded and unpadded shapes on CPU, gone in fp32 (the pod's replay dtype).

Observed, relevant to reading the 27B run: a RANDOM direction at lambda = +-0.4 (40 % of the mean residual norm, which
here is not outlier-inflated: mean 39.2k vs median 40.0k excluding BOS) collapses the 4B's option mass to 0 and its greedy
text to a repeated token. If 27B placebos do the same at +-0.4, their switching points are undefined there and lambda*
falls back to a smaller symmetric strength under the registered rule; coherence catches the target's own breakdown.
