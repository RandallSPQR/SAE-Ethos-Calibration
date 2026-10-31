import sys
from pathlib import Path

import sae_ethos_pipeline  # noqa: F401  (activates the pipeline's import root: lineup, modelcfg, model_io)

sys.path.insert(0, str(Path(__file__).resolve().parent))
