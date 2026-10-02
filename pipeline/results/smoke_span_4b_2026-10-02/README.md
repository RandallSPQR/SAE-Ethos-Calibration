# Span-scoring smoke on Gemma-3-4B-IT (2026-10-02, local CPU, $0): 4/4 PASS

`MODEL_PROFILE=gemma-3-4b-it T1_DTYPE=bfloat16 python -m replay.smoke_span_4b --n 4 --spans 3`; google/gemma-3-4b-it at
revision 093f9f38 (Gemma3ForConditionalGeneration, the 27B's class: 34-layer text decoder + 27-layer SigLIP vision tower);
torch 2.8.0, nnsight < 0.8 (the pod's pins); synthetic SAE. Inputs: 27B T3 dev spans (shared Gemma-3 tokenizer).
Output: `SMOKE_OUTPUT.txt`.

1. **Captured blocks are language-model layers:** 16, 21, 22, 25, 29 -> `model.language_model.layers.<i>`; the model has a
   vision tower (`model.vision_tower.vision_model.encoder.layers.*`) sharing those indices, so the check is real.
2. **Layer convention:** at every captured layer the residual of block L matches transformers'
   `output_hidden_states[L + 1]` at cosine 1.000 (minimum over positions) and `hidden_states[L]` at only 0.95-0.97: the
   off-by-one is real and distinguishable, and the replay path is on the right side of it.
3. **span_score end to end:** 3 spans scored (return code 0); G1 arrays equal length (generated ids = span tokens =
   replay logprobs = generation logprobs).

Runtime note: bf16 on a laptop CPU is ~10 min per span pass. The first attempt held two 8.6 GB model copies (the smoke's
and span_score's) and swapped (11.7 GB swap); the smoke now frees its copy first. On the pod the same path runs fp32 on GPU.
Nothing here is analysed: a different model, a synthetic SAE.
