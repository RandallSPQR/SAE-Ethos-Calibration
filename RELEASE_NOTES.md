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
