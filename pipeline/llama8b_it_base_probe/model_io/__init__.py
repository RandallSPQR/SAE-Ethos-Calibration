"""Llama-3.1-8B model_io adapters (`llama3`, `llama3_base`), registered with sae_ethos_pipeline's model_io through the
entry points in sae-ethos-pipeline's own pyproject.toml (plug-ins, not built-in FAMILIES), so `modelcfg.serializer()`
reaches them from a profile's `family:` field once the pipeline is installed.

Do not install beside another distribution that registers the same family names (the trust-geometry study does, until
it adopts this subpackage): model_io resolves an entry point by name and would take whichever it finds first."""
import os
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def tokenizer_dir() -> Path:
    """Where the OID-verified tokenizer files live (fetch_tokenizers writes them; tests skip without them)."""
    return Path(os.environ.get("LLAMA_PROBE_TOKENIZERS") or FIXTURES / "tokenizers").expanduser()
