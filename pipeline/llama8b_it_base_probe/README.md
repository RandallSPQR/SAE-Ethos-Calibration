# llama8b_it_base_probe (llama8B-IT-Base-Probe)

Readout scoring, hidden-state capture, run manifests, statistics and scoped RunPod tooling for **Llama-3.1-8B (base)**
and **Llama-3.1-8B-Instruct**. It was extracted from the trust-geometry study (`p0-gates` @ `620b3da`) and has been
part of sae-ethos-pipeline since 1.1.0.

The subpackage carries **machinery only**. A project defines its own readouts and re-establishes readout validity for
its own construct. Passing `tokens.check_readout` on both tokenizers is necessary, not sufficient. Behavioural validity,
gates, pre-registration and null calibration of the statistics on the project's own design are the project's work.

## Install and import

```bash
pip install "sae-ethos-pipeline[replay,analysis] @ git+https://github.com/RandallSPQR/SAE-Ethos-Calibration@v1.1.0"
```

```bash
llama-probe-fetch-tokenizers
```

The first command installs the GPU stack; on a pod, torch comes from the image. The second fetches the OID-verified
Llama tokenizer files (text only).

```python
import sae_ethos_pipeline                       # the pipeline's import root, as for every pipeline module
from llama8b_it_base_probe import capture, tokens
from llama8b_it_base_probe.spec import ProjectSpec, Readout
```

Profiles sit with the pipeline's others, in `config/models_llama-3.1-8b.yaml` and
`config/models_llama-3.1-8b-it.yaml`. Select one with `MODEL_PROFILE=llama-3.1-8b-it`. Their revisions and weight
hashes are the ones pinned on the study's pod (2026-10-10).

The Llama adapters are **plug-ins**: `sae_ethos_pipeline.model_io` entry points named `llama3` and `llama3_base`, not
built-in `FAMILIES`. An entry point exists only in an installed copy. **Don't install beside another distribution that
registers the same names** (the trust-geometry study does, until it adopts this). The registry takes whichever entry
point it finds first.

## What's in it

| module | what |
|---|---|
| `model_io` | `llama3` (template-exact Instruct serializer), `llama3_base` (plain-text stem), fixtures, `fetch_tokenizers` |
| `load` | `load_model(dtype)`: the pinned model, eager attention, TF32 off, tokenizer checked against the profile |
| `ops` | `answer_tokens`, `sequence_probs` (exact chains, end factor), `readout`, batched last-token `capture`, `Steerer` |
| `tokens` | `single_token_forms`, `check_single_token`, `check_answer_forms`, `check_readout`, `token_positions` |
| `score` | `continuation_ids`, `level_probs`, `summarize` |
| `spec` | `Readout`, `ProjectSpec` |
| `capture` | `run_capture(...)`; `llama-probe-capture --spec mod:SPEC --items ITEMS --out OUT [--readouts-only]` |
| `manifest` | provenance manifest + project identity + hooks + `artifacts` + `extra`, all inside `run_id`; `llama-probe-manifest` |
| `stats` | `perm` (Freedman–Lane on a coefficient, within-group perms), `decode` (held-out ΔAUC over a baseline with Freedman–Lane p), `fe` (FE OLS + CR2, t(G−1); 2SLS), `multi` (Holm, jackknife), `calib` (null false-pass rate, MDE) |
| `pods` | `llama-probe-pods`: scoped RunPod registry, deadline at create, pod-side self-stop, $ ceiling, launchd tick |
| `pod/common.sh` | pod setup: pinned venvs, HF token from PID 1, access check, fetch + hash preflight, vLLM serve |
| `cli` | the `llama-probe-*` console scripts; `llama-probe-run MODULE` runs any pipeline module as `__main__` |

The capture path is plain Transformers `output_hidden_states` (`hidden_states[L + 1]` = output of block L). It is
**not** the pipeline's nnsight path in `replay/hooks.py`; the two are separate, each with its own tests. A project
making replay-fidelity claims on this path validates it against served generation itself.

## A project supplies

1. **A `ProjectSpec`** (see `example/dry_weather/spec.py`): readouts per family, the primary readout per family,
   layers, `also_last`, and `spans(item)` for activation positions.
   - Cue readouts with single-token options are typical on base.
   - Answer readouts with spellings per level are typical on Instruct; set `exact_end=True` for multi-token labels.
2. **Items** (jsonl): `id`, `text`, whatever the spec renders, and what `spans` reads.
3. **Readout validity:** `tokens.check_readout` on both tokenizers for every readout and item (the example's test does
   this), then the project's own behavioural checks and gates.
4. **Statistics choices and their calibration:** grouping, statistic, B, α, correction; `stats.calib` on design-shaped
   nulls (the false-pass CI must cover α) and an MDE, before any real number.
5. **Pods:** a `pods.toml`, the RunPod key via `newkey <project> RUNPOD_API_KEY`, the HF token as a RunPod secret, and
   a driver script that sources `pod/common.sh`.
6. **Its own pre-registration and amendments.** Nothing here encodes a decision rule.

## Pods

```toml
# pods.toml, in the project root (next to the .envrc that newkey writes)
project = "my-study"
ceiling_usd = 50.0
max_hours = 2.5
gpu = "NVIDIA RTX A6000"
```

```bash
newkey my-study RUNPOD_API_KEY
```

```bash
direnv exec . llama-probe-pods --config pods.toml install
```

```bash
direnv exec . llama-probe-pods --config pods.toml create --name my-study-run1 --hours 1.5 --secret-env HF_TOKEN=hf_token --dry-run
```

The key comes from `$RUNPOD_API_KEY` only; no key file is read. The launchd tick runs
`direnv exec <project_root> python pods.py tick`, so it loads the key from Keychain the same way. `install` refuses if
`direnv exec` doesn't set it. A locked Keychain makes the tick fail loudly; the pod-side self-stop and the deadline
still end the pod.

## Tests (CPU)

Run from the repository root:

```bash
pytest -q pipeline/llama8b_it_base_probe/tests pipeline/llama8b_it_base_probe/example
```

The tests use a tiny random Llama and the real tokenizers, and skip anything that needs tokenizer files if they aren't
fetched. They need torch and transformers 5.17.0 (the `replay` extra, or those two alone) plus scipy and jinja2.
