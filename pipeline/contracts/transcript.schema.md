# Transcript contract

`generate/extract_transcripts.py` normalizes Inspect `.eval` logs (and resampled continuations) into
one JSONL file per (scenario, seed, variant). Every downstream consumer — replay, resample, gates,
analysis — reads this shape and nothing else. Freezing it here is what lets the GPU code and the
no-GPU gate code be written independently.

One JSON object per line, one object per **continuation**:

```json
{
  "uid": "honeypot_forbidden_table/seed_003/full/c07",
  "scenario": "honeypot_forbidden_table",
  "arm": "A",
  "seed": 3,
  "split": "discover",                    // from seed: 0-49 discover, 50-99 test
  "variant": "full",                      // full | control | mention   (Arm A) ; bad | twin (Arm B)
  "persona_prompted": "eager",            // eager | rigor | neutral   (2x2 prompted axis)
  "steer": {"vector": null, "strength": 0.0},   // set by resample/steer; null = unsteered
  "messages": [                           // full chat, in the target's template order
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [{"name":"read_file","args":{"path":"README.md"}}]},
    {"role": "tool", "name": "read_file", "content": "..."}
  ],
  "obstacle_revealed_at": 4,               // index of the tool result that reveals the obstacle
  "decision_point": 5,                     // TRUNCATION index: resampling drops messages[decision_point:] and regenerates
  "scored_message_index": 5,               // the assistant message whose activations are scored (see below)
  "tokens": {                              // filled by replay (teacher-forcing); absent pre-replay
    "ids": [...],                          // full teacher-forced input ids (whole conversation)
    "assistant_span": [812, 1043],         // [start,end) input-token index of the scored assistant turn
    "sampled_ids": [...],                  // generation: the ids the target emitted for the DECISION turn (continuation-relative)
    "sampled_logprobs": [...],             // generation: logprob of each sampled id, same length/order as sampled_ids
    // --- filled by replay, ALL continuation-relative and EQUAL LENGTH (this killed an alignment bug) ---
    "generated_ids": [...],                // == sampled_ids, restated in continuation coords for clarity
    "replay_predicted_ids": [...],         // replay argmax predicting each generated position (autoregressive shift handled in replay)
    "generation_logprob": [...],           // logprob of generated_ids[k] at generation time
    "replay_logprob": [...],               // logprob of the SAME token id under teacher-forced replay
    // --- TOKEN-LEVEL counts (positional/context effects are token phenomena, not message-count ones) ---
    "prompt_token_count": 812,             // tokens before the scored assistant turn
    "decision_token_position": 812,        // absolute token index where the scored turn begins
    "assistant_token_count": 231           // tokens in the scored assistant turn (the E[A] denominator)
  },
  "spans": {                               // MESSAGE indices; token spans added at replay for the scored one
    "decision_message_index": 5,           // the first choice after the obstacle
    "transgression_message_index": 8,      // the turn that performed the transgressive act (null if none)
    "terminal_message_index": 10           // the final assistant turn
  },
  "judge": {                               // TWO labels — never conflate them
    "decision_action_label": "ask",        // what the model did AT the decision turn (ask/submit/act_.../benign_step)
    "episode_outcome_label": "used_and_concealed",  // how the whole episode ended (from final state)
    "taxonomy": "deception_concealment",   // taxonomy of the episode outcome
    "label": "used_and_concealed",         // back-compat alias == episode_outcome_label
    "disclosed": false,
    "confidence": 0.82
  },
  "cost_usd": 0.014                        // per-continuation cost, summed into the ledger
}
```

Rules:
- `uid` is globally unique and is the join key to the feature store.
- `split` is derived from `seed`, never set by hand; `analyze/split.py` asserts discover/test never mix.
- **Two indices, two jobs — never overload one integer** (this was a real bug):
  - `decision_point` is the TRUNCATION index. `resample.py` builds the prefix `messages[:decision_point]`
    and regenerates, so the original decision turn is *replaced* by each sampled continuation.
  - `scored_message_index` is the index of the assistant message whose activations replay scores.
    On an ORIGINAL transcript it equals `decision_point`. On a RESAMPLED row the prefix has length
    `decision_point` and the new assistant message sits at index `decision_point`, so `resample.py`
    sets `scored_message_index = decision_point` for the new row explicitly. Replay always scores
    `messages[:scored_message_index + 1]` and never re-derives it from `decision_point`.
- `obstacle_revealed_at` comes from the scenario's `obstacle.revealed_by`.
- `tokens` is added only after replay. Pre-replay consumers (judge, resample) must not require it.
- `tokens.sampled_ids` and `tokens.sampled_logprobs` MUST be populated at generation time (from the
  target's returned token ids — use vLLM `return_token_ids`, not a fragile OpenAI logprob attribute).
  Missing raw ids is a HARD G1 failure, never a silent retokenize.
- **G1 alignment contract.** All four G1 arrays are continuation-relative (index 0 = first generated
  token of the decision turn) and EQUAL LENGTH. Replay is responsible for the autoregressive shift:
  `replay_predicted_ids[k]` is the argmax of the logits that PREDICT `generated_ids[k]` (i.e. from the
  logits at the preceding input position), and `replay_logprob[k]` is the logprob that replay assigns
  to `generated_ids[k]`. G1 never re-derives these from `assistant_span` slices of whole-sequence
  arrays — that mixed coordinate systems and was a bug.
- Arm A and Arm B transcripts live in separate directories (`transcripts/arm_a/`, `transcripts/arm_b/`)
  and are never concatenated.
