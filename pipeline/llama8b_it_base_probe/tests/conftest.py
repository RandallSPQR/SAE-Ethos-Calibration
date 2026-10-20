import sys
from pathlib import Path

import sae_ethos_pipeline  # noqa: F401  (activates the pipeline's import root: llama8b_it_base_probe, modelcfg, model_io)

sys.path.insert(0, str(Path(__file__).resolve().parent))
