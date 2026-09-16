# pipeline/

White-box replication pipeline for Mythos System Card §4.5.3–4.5.4, with a separate **replay** pass for
activations. Consumes the `scenarios/` package.

## Two generators, on purpose (this changed after review)

Arm A's labels depend on **real filesystem state** — a real edited test file, a real canary in a real
written file, real reference tests run on the final repo. Petri's tools are *auditor-simulated*, so the
auditor role-playing "reality refuses to satisfy the test" is exactly NOT what `impossible_test` needs:
you want reality to refuse, not a Dungeon Master narrating that it did. So:

- **Arm A (code behavior) → `harness/`, a real deterministic sandbox.** It executes the nine tools
  against the real rendered repo, and `harness/labeler.py` derives the transgression label from real
  final state, not a judge's guess. This is the measuring instrument.
- **Petri → exploratory scenario generation, and Arm B conversational audits.** Petri is excellent at
  *discovering* scenarios and at the disguised-harm conversational arm; it is not on the Arm-A
  measurement path. `seeds/build_seeds.py` and `generate/run_petri.py` remain for that role.

Both feed the same transcript contract, so replay/analysis don't care which produced a given transcript.

## The replay split (why it's shaped this way)

The white-box analysis reads the **token sequence that actually happened**. Whether tools were real
(harness) or simulated (Petri), replay teacher-forces the recorded messages and taps activations:

```
                      scenarios/ (rendered)                Andy's Level-6 items
                              │                                    │
                     seeds/build_seeds.py  ───────────────────────┘
                              │  writes Petri seed .md files
                              ▼
   STAGE 1  GENERATE
     Arm A:  harness/run_harness.py   (REAL sandbox: 9 tools on the rendered repo, deterministic labels)
     Arm B / exploration:  inspect eval inspect_petri/audit  ->  extract_transcripts.py
                              │  transcripts/*.jsonl   (one contract; decision point + scored_message_index)
                              ├──► resample/resample.py  (harness snapshots the real FS; Petri path truncates messages)
                              ▼
   STAGE 2  REPLAY     replay/replay.py   (nnsight-in-vLLM teacher-force @ hook_resid_post, layer 31)
                              │  features/*.parquet   (sparse SAE activations)  + oracle explanations
                              ▼
   ANALYZE            analyze/  (discover/test split, Cohen's d, steering sweeps, base-vs-IT)
```

One served model via nnsight's in-vLLM backend feeds generation, resampling, and intervened replay —
no second Transformers copy, no instrument mismatch. That's the "same model as several personas + a
watcher" design in one process.

## Gates block the run

`gates/` holds the instrument gates. G0–G5 are the T1 calibration ladder (run in order on the dev
model, cheapest signal first); G6–G8 are analysis gates that run on saved outputs without a GPU.
`gates/run_gates.py` exits non-zero on any failure and the experiment Makefile targets depend on it.
**The pipeline will not spend money past a red gate.**

| Gate | Name | What it proves | Needs GPU? |
|------|------|----------------|-----------|
| G0 | model_checksum | greedy vLLM generation == greedy nnsight-in-vLLM (same computation, both paths) | yes |
| G1 | replay_fidelity | T=0: sampled ids == replay argmax (exact checksum); T>0: sampled ids high-logprob under replay | yes |
| G2 | sae_health | variance-explained & L0 on our transcripts vs Gemma Scope reported | yes |
| G3 | feature_known_answer | labeled features fire where they should; planted-concept test | yes |
| G4 | steering_known_answer | a published steering effect reproduces with a dose-response curve | yes |
| G5 | oracle_calibration | oracle accuracy on labeled activations; confabulation rate on zeroed input | yes |
| G6 | judge_agreement | judge vs 50 hand labels, κ ≥ 0.70 | no |
| G7 | scenario_base_rates | control solvable ≥ 0.5; full transgression in [0.1, 0.7] | no (uses judge output) |
| G8 | null_controls | permuted labels flatten Cohen's d; prefix-length matched | no |

G6/G7/G8 run without a GPU on saved outputs, so they gate the analysis code today. G0–G5 run on the
dev model (Gemma-2-9B) the first time you rent a box, in order. Each gate has a **fixture mode**
(`--fixture`) that runs its logic on tiny synthetic data with no heavy deps, so the gate *code* is
testable now. The T1 order and the "code feature discriminates code vs prose" first experiment are in
`RUNBOOK.md`.

## Config

- `config/models.yaml` — the three model roles and the vLLM endpoint. Nothing else references model
  strings, so switching Gemma-2-9B → Gemma-3-27B is a one-file edit.
- `config/run.yaml` — sampling, layer fraction, gate thresholds, cost ceiling + kill switch.

## Layout

```
pipeline/
  README.md  pyproject.toml  Makefile
  config/    models.yaml  run.yaml
  contracts/ transcript.schema.md  feature_store.schema.md   # the two data contracts everything shares
  harness/   sandbox.py tools.py protocol.py agent_loop.py labeler.py run_harness.py  # REAL Arm-A instrument
  seeds/     build_seeds.py                                     # Petri seeds (exploration / Arm B)
  generate/  run_petri.py  extract_transcripts.py
  resample/  resample.py  target_client.py
  replay/    modelload.py  hooks.py  sae.py  oracle.py  replay.py
  analyze/   split.py  effects.py  steer.py
  gates/     g0_model_checksum.py g1_replay_fidelity.py ... g8_null_controls.py  run_gates.py
  smoke/     smoke_test.py
```

## Status

Scaffold. Glue, contracts, gate logic, seed builder, and cost ledger are **real and runnable**
(`make gates-nogpu`, `make smoke-fixture`). The GPU-bound loads (`replay/modelload.py`, `sae.py`,
`oracle.py`, the Petri subprocess) are **stubs with correct signatures and docstrings** — marked
`# STUB` — so the shape is fixed and only the bodies need filling once a box is rented.
