# Fixture provenance (2026-10-09)

The meta-llama repos are gated (manual approval) and this HF account got 403 on their files. The HF tree API still lists
each file's git blob OID at the pinned revision, so every file here was fetched from an ungated mirror and accepted only
if `sha1("blob <len>\0" + bytes)` equals the gated file's OID (the Gemma-3 profile's method).

| file | gated repo @ revision | blob OID | fetched from |
|---|---|---|---|
| tokenizer_config.json | meta-llama/Llama-3.1-8B-Instruct @ 0e9e39f249a1 | db88166e2bc4c799fd5d1ae643b75e84d03ee70e | hugging-quants/Meta-Llama-3.1-8B-Instruct-AWQ-INT4 (main) |
| tokenizer.json | meta-llama/Llama-3.1-8B-Instruct @ 0e9e39f249a1 | 5cc5f00a5b203e90a27a3bd60d1ec393b07971e8 | NousResearch/Meta-Llama-3.1-8B-Instruct (main) |
| config.json | meta-llama/Llama-3.1-8B-Instruct @ 0e9e39f249a1 | 0bb6fd75b3ad2fe988565929f329945262c2814e | NousResearch/Meta-Llama-3.1-8B-Instruct (main) |
| generation_config.json | meta-llama/Llama-3.1-8B-Instruct @ 0e9e39f249a1 | cc7276afd599de091142c6ed3005faf8a74aa257 | NousResearch/Meta-Llama-3.1-8B-Instruct (main) |
| special_tokens_map.json | meta-llama/Llama-3.1-8B-Instruct @ 0e9e39f249a1 | 02ee80b6196926a5ad790a004d9efd6ab1ba6542 | NousResearch/Meta-Llama-3.1-8B-Instruct (main) |
| tokenizer_config.json | meta-llama/Llama-3.1-8B @ d04e592bb4f6 | cb9ec25536e44d86778b10509d3e5bdca459a5cf | NousResearch/Meta-Llama-3.1-8B (main) |
| tokenizer.json | meta-llama/Llama-3.1-8B @ d04e592bb4f6 | f916e71031fa08f3c6ef1680a590c15b52d3cdd9 | NousResearch/Meta-Llama-3.1-8B (main) |
| config.json | meta-llama/Llama-3.1-8B @ d04e592bb4f6 | cccf055d6f8f210387a248c91dc40e0c7a4bafab | NousResearch/Meta-Llama-3.1-8B (main) |
| generation_config.json | meta-llama/Llama-3.1-8B @ d04e592bb4f6 | fc9506438b7b55383dc04c0816561442324846c3 | NousResearch/Meta-Llama-3.1-8B (main) |
| special_tokens_map.json | meta-llama/Llama-3.1-8B @ d04e592bb4f6 | d8cd5076496dbe4be2320312abc10adc43097b81 | NousResearch/Meta-Llama-3.1-8B (main) |

The Instruct tokenizer_config.json on the NousResearch and unsloth mirrors did NOT match (they carry an earlier
chat template); only the AWQ mirror above carries Meta's current blob.

`llama3.1-8b-instruct_chat_template.jinja` is the `chat_template` string of the verified Instruct tokenizer_config.json,
written verbatim; its sha256[:16] is `e10ca381b1ccc5cf` (the profile's `chat_template_hash`, computed as
sae_ethos_pipeline's provenance computes it).

`tokenizers/` (gitignored; Llama 3.1 Community License, not redistributed here) is rebuilt by
`llama-probe-fetch-tokenizers`, which re-checks every OID above.
