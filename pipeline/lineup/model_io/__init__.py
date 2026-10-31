"""model_io adapters added by the lineup subpackage (since 1.2.0): `qwen3_5` (Qwen3.5-9B / Qwen3.5-27B, thinking off),
registered with sae_ethos_pipeline's model_io through the entry point in sae-ethos-pipeline's pyproject.toml, so
`modelcfg.serializer()` reaches it from a profile's `family:` field once the pipeline is installed."""
import os
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def tokenizer_dir() -> Path:
    """Where the verified Qwen3.5 tokenizer files live (lineup-fetch-tokenizers writes them; tests skip without them)."""
    return Path(os.environ.get("LINEUP_TOKENIZERS") or FIXTURES / "tokenizers").expanduser()
