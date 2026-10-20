"""llama8b_it_base_probe (llama8B-IT-Base-Probe): readout scoring, hidden-state capture, run manifests, statistics and
scoped pod tooling for Llama-3.1-8B (base) and Llama-3.1-8B-Instruct, extracted from the trust-geometry study
(p0-gates @ 620b3da). Part of sae-ethos-pipeline since 1.1.0; imported by its top-level name, like the rest of the
pipeline:

    import sae_ethos_pipeline                  # activates the pipeline's import root
    from llama8b_it_base_probe import capture, tokens
    from llama8b_it_base_probe.spec import ProjectSpec, Readout

The subpackage carries machinery only. A project supplies its own readout spec (spec.ProjectSpec) and re-establishes
readout validity for its own construct (tokens.check_readout on both tokenizers, then its own behavioural checks and
gates); nothing here certifies a readout.

  model_io     llama3 / llama3_base serializers (sae_ethos_pipeline.model_io entry points), tokenizer fixtures
  load         the pinned model on the Transformers path (eager attention, TF32 off)
  ops          answer_tokens, sequence_probs, readout, batched last-token capture, Steerer
  tokens       single-token forms, readout checks, char spans -> token positions
  score        continuation_ids, level_probs (exact multi-token scoring with the end-of-label factor), summarize
  spec         Readout / ProjectSpec
  capture      readouts and hidden states per item (llama-probe-capture)
  manifest     provenance + project identity + hooks + artifacts, inside run_id (llama-probe-manifest)
  stats        within-group permutation (Freedman-Lane), held-out decoding, FE OLS with CR2, 2SLS, Holm, jackknife,
               null calibration
  pods         scoped RunPod tool (llama-probe-pods), pod/common.sh
  cli          the console scripts
The profiles live with the pipeline's others: config/models_llama-3.1-8b.yaml and models_llama-3.1-8b-it.yaml
(MODEL_PROFILE=llama-3.1-8b[-it], or MODEL_PROFILE_FILE=profile_path(...)).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT.parent / "config"
PROFILES = {"llama-3.1-8b": "models_llama-3.1-8b.yaml", "llama-3.1-8b-it": "models_llama-3.1-8b-it.yaml"}


def profile_path(name: str) -> Path:
    """Absolute path of a shipped profile ('llama-3.1-8b' or 'llama-3.1-8b-it'), for MODEL_PROFILE_FILE."""
    return CONFIG / PROFILES[name]


def version() -> str:
    import sae_ethos_pipeline
    return sae_ethos_pipeline.__version__
