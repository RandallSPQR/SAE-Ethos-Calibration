"""lineup: the shared five-model lineup for Stigmergic Phase Transitions (StPT) and Missing Piece, added in
sae-ethos-pipeline 1.2.0. Imported by its top-level name, like the rest of the pipeline:

    import sae_ethos_pipeline                  # activates the pipeline's import root
    import lineup
    lineup.models()                            # the five models, their profiles and pinned identities

What it adds (additive; nothing existing changes behaviour):
  model_io     the qwen3_5 serializer (Qwen3.5-9B / Qwen3.5-27B, thinking off), tokenizer fixtures with provenance
  lineup.yaml  the lineup: five models -> profile, revision, weight hash, chat-template hash, family; the serving stack
  identity     offline identity check against the Hugging Face API (revision, weight_hash from LFS metadata,
               chat_template_hash) and the box-side check (preflight + tokenizer facts + template agreement)
  serve        the vLLM command line for the lineup stack (batch size 1, prefix caching off, raw logprobs, seeded)
  readout      exact binary-choice readout (log P(A), log P(B), non-A/B mass) from a vLLM server, and a repeat probe
  manifest     the identity block every StPT / Missing Piece run manifest embeds (pipeline version + commit, model,
               serving stack)

The lineup is LOCKED at the 1.2.0 tag: any change to a pinned value, the serializer or the serving stack is a new
pipeline version and a logged deviation in the shared coordination record (RELEASE_NOTES.md).
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT.parent / "config"
LINEUP_FILE = ROOT / "lineup.yaml"


def lineup() -> dict:
    return yaml.safe_load(LINEUP_FILE.read_text())


def models() -> dict:
    """name -> model entry (profile, hf_id, revision, weight_hash, chat_template_hash, family, ...)."""
    return {m["name"]: m for m in lineup()["models"]}


def profile_path(name: str) -> Path:
    """Absolute path of a lineup model's profile, for MODEL_PROFILE_FILE."""
    return CONFIG / models()[name]["profile"]


def version() -> str:
    import sae_ethos_pipeline
    return sae_ethos_pipeline.__version__
