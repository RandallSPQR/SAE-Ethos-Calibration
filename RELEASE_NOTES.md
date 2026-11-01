# REJECTED CANDIDATE, NOT RELEASED: do not install this branch (2026-11-01)

The confirmation run (StPT gate S0, pod s5itxbomssdjz1, this branch's commit ffc4902a1db0 installed from GitHub, 3 cold
server starts per model on an RTX PRO 6000 Blackwell) failed:

| model | across cold starts | ms per call |
|---|---|---|
| Llama-3.1-8B | identical | 28.0 |
| Gemma-2-9B | drifts (0.125) | 37.8 |
| Qwen3.5-9B | drifts (0.080) | 34.4 |
| Gemma-3-27B | cannot start: FLASH_ATTN is invalid for its multimodal configuration ('partial multimodal token full attention not supported') | none |
| Qwen3.5-27B | identical | 85.8 |

No `v1.2.1` tag exists. The studies stay on v1.2.0. The text below is the candidate as proposed, kept as the record.

# (candidate) sae-ethos-pipeline v1.2.1: deterministic serving across server starts (2026-11-01)

**Supersedes v1.2.0 for StPT and Missing Piece.** Studies pinned to v1.2.0 stay valid as records, but data for either
study is collected only under v1.2.1. Models, revisions, weight hashes, serializers and readout are unchanged; only the
serving flags and one pod-script line change.

## Why

StPT gate S0 (2026-10-31, RTX PRO 6000 Blackwell) found the v1.2.0 stack bit-identical WITHIN a server start but not
ACROSS starts. Four of five models moved by 1–2 bf16 steps on the A/B logit, up to 0.25 nats. A restart test with
every compile and autotune cache cleared before each start screened four settings on Llama-3.1-8B and Qwen3.5-9B:

| setting | Llama-8B | Qwen3.5-9B |
|---|---|---|
| autotuning off (inductor max-autotune, coordinate descent, FlashInfer) | drifts | drifts |
| `--enforce-eager` | identical | drifts |
| `VLLM_BATCH_INVARIANT=1` + `--attention-backend FLASH_ATTN` | drifts | drifts |
| **both of the above** | **identical** | **identical** |

Records: https://github.com/RandallSPQR/Stigmergic-Phase-Transitions/tree/main/gates/S0 (`S0_REPORT.md` 4b, artifacts).

## What changes

- **`lineup.yaml`:**
  - serving flags gain `--enforce-eager` and `--attention-backend FLASH_ATTN`, and env gains `VLLM_BATCH_INVARIANT=1`;
  - `gpu_type` names the GPU type on which determinism is established;
  - the Qwen pair's identity status becomes `pod_pinned`, because its bytes were re-hashed to the pins on GPU at S0;
  - `lineup_version` becomes 1.2.1.
- **`pipeline/lineup/pod/serve_lineup.sh`:** calls the `lineup-serve-cmd` console script. In 1.2.0 it called
  `python -m lineup.serve`, which cannot import `lineup` from an installed copy.
- Version 1.2.1 (`pyproject.toml`, `__init__.py`); lineup docstring; tests assert the new flags.

## Cost

Per call, at batch size 1 (restart test): Llama-8B 15.4 → 28.4 ms, Qwen3.5-9B 25.7 → 34.5 ms.

## Confirmation on all five models (GPU, this commit installed from GitHub)

FAILED: see the top of this file.

# sae-ethos-pipeline v1.2.0: the StPT / Missing Piece five-model lineup (2026-10-31)

**Additive.** v1.2.0 adds the `lineup` subpackage and the Qwen3.5 pair. Existing behaviour does not change: the SAE
studies' serving (`calibrate/pod_bootstrap.sh`, vLLM 0.11.0) and every existing profile are untouched. A study pinned to
v1.0.0 or v1.1.0 is unaffected.

**Locked.** This is the shared pipeline of Stigmergic Phase Transitions (StPT, rung 1) and Missing Piece. Both studies
pin tag `v1.2.0` and its commit hash in every manifest (`lineup.manifest.identity_block`, `check_pin`). Any change to
a pinned value, a serializer or the serving stack is a new version and a logged deviation in the shared coordination
record. Nothing is edited in place.

## The lineup (`pipeline/lineup/lineup.yaml`)

| model | profile | family | revision | weight_hash | chat_template_hash | identity |
|---|---|---|---|---|---|---|
| Llama-3.1-8B-Instruct | `models_llama-3.1-8b-it.yaml` | llama3 | 0e9e39f249a1 | 495b73750141622d | e10ca381b1ccc5cf | pod-pinned 2026-10-10 |
| Gemma-2-9B-IT | `models.yaml` | gemma2 | 11c9b309abf7 | 7055bc8c5f456ced | ecd6ae513fe103f0 | pod-pinned 2026-09-18 |
| Gemma-3-27B-IT | `models_gemma-3-27b-it.yaml` | gemma3 | 005ad3404e59 | 34f745e7c6ef5b7b | 7de1c58e208eda46 | pod-pinned 2026-09-29 |
| **Qwen3.5-9B** (thinking off) | `models_qwen3.5-9b.yaml` (new) | qwen3_5 (new) | c202236235762 | 4b570a1e6e5db4b9 | a4aee8afcf2e0711 | metadata-pinned 2026-10-31 |
| **Qwen3.5-27B** (thinking off) | `models_qwen3.5-27b.yaml` (new) | qwen3_5 (new) | fc05daec18b0 | 5e536c8708a9430b | a4aee8afcf2e0711 | metadata-pinned 2026-10-31 |

- **The Qwen pair is pencilled** in both briefs. Both are dense (no `num_experts`) and share one hybrid architecture:
  3 Gated DeltaNet layers to 1 full-attention layer, 32 layers at 9B and 64 at 27B. So the size contrast does not also
  change architecture, and the brief's mixture-of-experts confound does not arise.
- **How the Qwen weight hashes were pinned.** `lineup.identity.weight_hash_from_files` applies the exact
  `preflight_weights` identity to the revision's recorded per-file hashes from the HF API.
  - Check: it reproduces all three pod-measured pins exactly (`tests/test_lineup.py`, against the snapshot in
    `tests/data/hf_identity_2026-10-31.json`).
  - Check: it equals `preflight_weights.verify_repo` on a fake snapshot.
  - Still to do: the box re-hash of the Qwen bytes (`pod/serve_lineup.sh`) is the S0 step, not yet run.
- **Live check (`lineup-identity offline`, 2026-10-31).** All five revisions resolve, and every weight_hash and
  chat_template_hash recomputes to its pin. Report: `pipeline/lineup/identity_reports/offline_2026-10-31.json`.

## What is new

- **`qwen3_5` serializer** (`lineup/model_io/qwen3_5.py`, entry point `qwen3_5`). It writes ChatML with thinking
  OFF; the generation prompt ends `<think>\n\n</think>\n\n`, and there is no BOS. Tests check it equals Qwen's own
  template (`enable_thinking=False`) on 11 conversations × with/without a generation prompt, as strings and as token
  ids. The template fixture is OID-verified (`fixtures/PROVENANCE.md`). Tokenizer files are fetched at the pinned
  revision and identity-checked by `lineup-fetch-tokenizers` (gitignored).
- **Identity checks** (`lineup-identity local|offline`; box: `pod/serve_lineup.sh`).
  - Local: `lineup.yaml` agrees with every profile, and every serializer is registered.
  - Offline: the HF API recomputation above.
  - Box: `preflight_weights --hash`, `check_tokenizer`, `template_agreement`, an 8-request bit-identity probe, and the
    exact-fallback consistency check.
- **One serving stack for all five models** (`lineup.yaml serving`; `lineup-serve-cmd NAME`; venv `venv_vllm_lineup`).
  - Pins: vLLM 0.19.1, torch 2.10.0, transformers 5.5.3, tokenizers 0.22.2, huggingface-hub 1.10.2. vLLM 0.11.0
    cannot serve Qwen3.5. At 0.19.1 the vLLM registry carries all five architectures, and the other pins are vLLM's
    own test pins at that tag.
  - Flags: batch size 1 (`--max-num-seqs 1`), prefix caching off, no prompt chunking, `--logprobs-mode raw_logprobs`,
    `--seed 0`, bf16. Multimodal checkpoints are served text-only.
  - All five are served in bf16. This includes Gemma-2-9B, which the closed SAE study served in fp32: that served
    dtype is part of that study's instrument, not of the lineup.
  - "Deterministic kernels" is established per model and GPU type by the box probe, not assumed.
- **Exact binary readout** (`lineup.readout`).
  - It sends the canonical token ids to `/v1/completions` and takes the top-20 raw logprobs keyed by token id.
  - An option outside the top 20 is read exactly by a second request restricted with `allowed_token_ids`.
  - It reports the non-option mass, `logit_AB`, and the top 20 for audit.
  - Decision noise and sampling are the study's protocol, not the pipeline's.
- **Identity block** (`lineup.manifest`, `lineup-identity-block NAME`). Every run manifest embeds the pipeline version,
  commit and tag; the lineup sha256; the model's pins; the profile sha256; the serializer source sha256; and the
  serving pins. `check_pin` refuses a run on any other commit or on a dirty checkout.
- **Profiles:** `config/models_qwen3.5-9b.yaml`, `config/models_qwen3.5-27b.yaml`. They carry no SAE or oracle. Hooks
  (resid_post at depth 0.25/0.5/0.75) were read from transformers 5.17 and vLLM 0.19.1 source; no activation has been
  captured yet.

## Changed existing files (additive)

- `modelcfg.template_agreement` passes the profile's optional `target_model.chat_template_kwargs` to
  `apply_chat_template` (Qwen needs `enable_thinking=False`). Without it, the call is unchanged.
- `calibrate/preflight_weights.default_repos` accepts a profile without `sae` or `oracle` (it verifies the target only).
  Profiles with an SAE are unchanged.
- `pyproject.toml` and `__init__.py`: version 1.2.0, the new packages, the `qwen3_5` entry point, and the console
  scripts `lineup-identity`, `lineup-serve-cmd`, `lineup-identity-block` and `lineup-fetch-tokenizers`.
- `.gitignore`: the Qwen tokenizer fixtures.

## Tests (CPU, 2026-10-31, Python 3.13, transformers 5.17.0)

- `pytest pipeline/lineup/tests`: 59 pass.
- `model_io.test_registry`: 6/6. `model_io.test_gemma3`: 12/12. `test_modelcfg`: 24/24 (the two `model_env_*` checks
  pass from this interpreter).
- `pytest pipeline/llama8b_it_base_probe/tests/test_model_io.py`: 30 pass. This covers `template_agreement` after the
  change above.
- The rest of the Llama suite needs torch and was not re-run; its code is unchanged.

## Release check

A wheel was built from the tagged tree and installed in a clean venv. It imports `sae_ethos_pipeline` (1.2.0) and
`lineup` (five models), resolves `qwen3_5` through the entry point, and ships `lineup.yaml`, the template fixture and
`pod/serve_lineup.sh`. It ships no tests and no data.

## Not done in this release (no GPU spend)

- The box identity check (`pod/serve_lineup.sh`) has not run for any model on the new serving stack.
- Until it runs, the Qwen weight hashes are metadata-pinned, and the serving determinism of all five models on vLLM
  0.19.1 is unverified.
- It is the first step of each study's S0.

# sae-ethos-pipeline v1.1.0 — Llama-3.1-8B probe subpackage (2026-10-20)

**Additive.** v1.1.0 adds new files and changes no existing behaviour. A study pinned to v1.0.0 is unaffected until it
chooses to upgrade.

- **`llama8b_it_base_probe`** (`pipeline/llama8b_it_base_probe/`, README there): readout scoring, hidden-state capture,
  run manifests, statistics and scoped RunPod tooling for Llama-3.1-8B base and Instruct. It was extracted from the
  trust-geometry study (`p0-gates` @ `620b3da`), and its capture output is byte-identical to the study's `capture_v2`
  (parity harness in that repo). Import it by its top-level name after `import sae_ethos_pipeline`.
- **Llama adapters as plug-ins:** the `sae_ethos_pipeline.model_io` entry points `llama3` and `llama3_base` in this
  `pyproject.toml`. The built-in `FAMILIES` stay `gemma2` and `gemma3`.
- **Profiles:** `config/models_llama-3.1-8b.yaml` and `config/models_llama-3.1-8b-it.yaml`
  (`MODEL_PROFILE=llama-3.1-8b[-it]`).
- **Console scripts:** `llama-probe-capture`, `llama-probe-manifest`, `llama-probe-fetch-tokenizers`,
  `llama-probe-pods` (RunPod key from `$RUNPOD_API_KEY` only; launchd tick through `direnv exec`), and
  `llama-probe-run MODULE`.
- **Changed existing files:**
  - `pyproject.toml` and `__init__.py`: the version, the new packages, entry points and scripts.
  - `.gitignore`: the Llama tokenizer fixtures.
  - `model_io/test_registry.py`: its "unknown family" probe now uses `no_such_family`, because `llama3` is a real
    plug-in once 1.1.0 is installed.
- **Tests (CPU):**
  - `pytest pipeline/llama8b_it_base_probe/tests pipeline/llama8b_it_base_probe/example`: 102 pass.
  - `model_io.test_registry`: 6/6. `model_io.test_gemma3`: 12/12.
  - `test_modelcfg`: 22/24. The two `model_env_*` checks fail identically on v1.0.0 `main` when run from a venv. The
    test's `python` shim symlinks the venv interpreter, which then loses the venv's site-packages. This is
    pre-existing and unrelated to this release.

# sae-ethos-pipeline v1.0.0 — code release (2026-10-09)

The `pipeline/` directory of SAE-Ethos-Calibration as an installable Python package, so another repository can depend on a
fixed version of the harness, labeler, gates, provenance, replay and analysis code. **Code only:** no runs, no results,
no weights, no data (see "Deliberately excluded").

## Install

```bash
pip install "git+https://github.com/RandallSPQR/SAE-Ethos-Calibration@v1.0.0"
# with the pinned replay / probe stack (torch 2.8.0, nnsight 0.7.0, transformers 5.17.0, accelerate 1.15.0)
pip install "sae-ethos-pipeline[replay] @ git+https://github.com/RandallSPQR/SAE-Ethos-Calibration@v1.0.0"
```

Python ≥ 3.9. Core dependencies are `pyyaml` and `numpy`; extras: `replay`, `tokenizer` (transformers + jinja2, for the
tokenizer and chat-template checks), `analysis` (scipy).

## Use

The package installs as one top-level package, `sae_ethos_pipeline`, whose directory is `pipeline/`. The pipeline's
modules import each other by top-level name (`import modelcfg`, `from harness import ...`), because on a pod they run from
that directory. Importing the package puts its directory on `sys.path`, so the same names work from an installed copy:

```python
import sae_ethos_pipeline             # activates the pipeline's import root
import provenance, modelcfg
from gates import run_gates, _common
print(_common.GATE_RULES_VERSION)     # 2026-10-06.1
```

Use the top-level names (`gates`, `harness`, …), not `sae_ethos_pipeline.gates`: the code's own imports use the
top-level names, and two names for one file would load it twice. The top-level names are generic (`harness`, `probe`,
`analyze`), so a consumer with modules of the same names should import this package in its own process or venv.

## Supported models

| model | profile | family adapter | status |
|---|---|---|---|
| **Gemma-2-9B-IT** | `config/models.yaml` (the default, `MODEL_PROFILE` unset) | `model_io.gemma2` | the 9B study, closed 2026-09-29 |
| **Gemma-3-27B-IT** | `config/models_gemma-3-27b-it.yaml` (`MODEL_PROFILE=gemma-3-27b-it`; revision 005ad3404e59, served bf16 by vLLM, replay pins above) | `model_io.gemma3` | current (items 5–10) |
| Gemma-3-4B-IT | `config/models_gemma-3-4b-it.yaml` | `model_io.gemma3` | local smoke tests only, not a study model |

## Adding a model family from outside (e.g. Llama-3.1-8B)

Model-family code lives only in the `model_io` adapters; everything else reads the profile through `modelcfg`. In v1.0.0
the last two Gemma literals outside the adapters (in `replay/oracle.py` and `replay/replay.py`) were moved behind them,
byte-identical for both Gemma families. Another package can add a family without editing this repo:

1. **An adapter module** with `FAMILY`, `END_OF_TURN`, `TURN_SUFFIX`, `GENERATION_PROMPT`, `user_turn(text)`,
   `serialize_messages(messages, add_generation_prompt=True)`, `apply_to_tokenizer(tokenizer, messages, ...)` and
   `prompt_hash(messages)` (the interface is in `model_io/__init__.py`; `model_io.check_adapter` enforces it).
2. **Register it**, either at runtime with `model_io.register("llama3", my_adapter)` or by an entry point in the
   consumer's own `pyproject.toml`:
   ```toml
   [project.entry-points."sae_ethos_pipeline.model_io"]
   llama3 = "my_pkg.llama3_adapter"
   ```
3. **A profile file** shipped by the consumer, selected with `MODEL_PROFILE_FILE=/abs/path/models_llama-3.1-8b-it.yaml`
   (it takes precedence over `MODEL_PROFILE`; its `family:` names the adapter). The profile states the stop token ids,
   decoder-layer path, hook names, replay dtype and serving settings; `modelcfg.check_tokenizer` and
   `modelcfg.template_agreement` verify them against the real tokenizer on the box, and G0/G1 gate the replay.

Tested: `model_io/test_registry.py` (runtime registration and an outside profile), and the release check below, which
installs a separate package whose entry point supplies a `llama3` adapter.

## Rule versions in this release

| rules | version | where |
|---|---|---|
| gates | **2026-10-06.1** | `gates/_common.py` GATE_RULES_VERSION, `gates/CHANGELOG.md` |
| labeler | **2026-10-02.1** | `harness/labeler.py` LABELER_RULES_VERSION, `harness/LABELER_CHANGELOG.md` |
| harness treatment | latest **2026-10-08.4** (disjoint episode uids for parallel cells), 2026-10-08.3 (no editor droppings in rendered repos) | `harness/CHANGELOG.md` |
| text-effect analysis | **2026-10-07.3** (degenerate-CI flag, stemmed echo / inference) | `analyze/item7b_text_effect.py` |
| item pre-registrations | item 7: 2026-10-07.1 / .2; item 8: 2026-10-08.1; item 9: 2026-10-08.2; item 10: 2026-10-09.1 | `analyze/PREREG_ITEM*.md` |

Item 10 was running on a pod when this release was cut; its code is the registered code (`harness/run_item10.py`,
`analyze/item10_grader.py`), and its results are not part of this release.

## Known open items

- **The greedy prefix is not reproducible across sessions.** In the 9B deep resample, seed 6's greedy prefix diverged
  between sessions; it was not localized, because excluded prefixes are not stored
  (`pipeline/results/t3_2026-09-29_deep/README.md`). Every run builds its prefixes fresh and records them in its rows;
  nothing compares prefixes across runs.
- **The mock backend assigns no episode uids**, so mock tests alone cannot catch uid collisions between cells run at once;
  item 9 attempt 1 hit exactly that. `harness/test_item9.py` now records the sandbox slots the real harness path builds.
- **Scenarios are not in the package.** The harness, the renderer and gate G7 read `scenarios/` beside `pipeline/`
  (`ROOT.parent / "scenarios"`), so running episodes or rendering needs a checkout of this repository at the same tag.
  The gates, provenance, modelcfg, model_io and analysis modules import and run without it.
- **STEERING_PROTOCOL_V2** is drafted and unregistered; steering pods are parked until a candidate variable is designed.
- Further audit items are listed in `PRE_27B_AUDIT.md`; they were not re-verified for this release.

## Deliberately excluded

- `pipeline/runs/`, `pipeline/results/`, `pipeline/build/`, `pipeline/scratchpad/`: run outputs, results, rendered builds.
- **Model weights** (downloaded from Hugging Face on the box, checked by `calibrate/preflight_weights.py`).
- **All data:** transcripts, labels, hand labels and the held-out matched-pair set. **The dataset is not published in this
  release;** it is a separate release later.
- `scenarios/` (see "Known open items").

The repository itself (including `pipeline/results/`) is unchanged by this release; the exclusions apply to the package.

## Release check

A clean venv installed the tag with `pip install git+file://…@v1.0.0` and imported `sae_ethos_pipeline`, `provenance`,
`modelcfg`, `gates.run_gates` and `gates._common`; read the gate rules version from the installed copy; confirmed that the
installed tree holds no `results/`, `runs/` or data files; and resolved a `llama3` adapter supplied by a separately
installed package through the entry point.
