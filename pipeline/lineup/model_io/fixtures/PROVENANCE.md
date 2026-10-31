# Fixture provenance (2026-10-31)

Both Qwen3.5 repositories are public (Apache-2.0, not gated). Every value here was read from the Hugging Face API at the
pinned revision on 2026-10-31.

| model | revision | lastModified | architecture |
|---|---|---|---|
| Qwen/Qwen3.5-9B | c202236235762e1c871ad0ccb60c8ee5ba337b9a | 2026-03-02 | Qwen3_5ForConditionalGeneration, dense, 32 layers (24 Gated DeltaNet + 8 full attention), d 4096 |
| Qwen/Qwen3.5-27B | fc05daec18b0a78c049392ed2e771dde82bdf654 | 2026-04-24 | Qwen3_5ForConditionalGeneration, dense, 64 layers (48 Gated DeltaNet + 16 full attention), d 5120 |

Both are dense (`num_experts` absent) with the same hybrid layer pattern, so the 9B/27B contrast is a size contrast
within one architecture (the brief's mixture-of-experts confound does not arise).

`qwen3.5_chat_template.jinja` is `chat_template.jinja` at the 9B revision, written verbatim (git blob OID
`a585dec894e63da457d9440ec6aa7caa16d20860`). The 27B revision's `chat_template.jinja` and the `chat_template` string in
both `tokenizer_config.json` files are byte-identical to it. Its sha256[:16], computed as sae_ethos_pipeline's provenance
computes `chat_template_hash`, is `a4aee8afcf2e0711` (both profiles).

Tokenizer files (`tokenizer.json` LFS sha256 `5f9e4d4901a92b99...`, `tokenizer_config.json` `eda48d3e75a8`,
`vocab.json` `0aa0ce0658d6`, `merges.txt` `a494e019ca15`) are byte-identical at both revisions.

Token facts (tokenizer_config.json): no BOS (`bos_token` null, `add_bos_token` false); `eos_token` `<|im_end|>` (248046);
`pad_token` `<|endoftext|>` (248044). The 27B's generation_config.json lists eos [248046, 248044]; the 9B revision ships no
generation_config.json. The profiles state stop_token_ids [248046], which is what modelcfg.check_tokenizer requires
({id(END_OF_TURN), eos_token_id}); `<|endoftext|>` cannot occur in a tool-free single-turn prompt.

`tokenizers/` (gitignored) is rebuilt by `lineup-fetch-tokenizers`, which re-checks every identity above.
