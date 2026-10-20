import sys
from pathlib import Path

import sae_ethos_pipeline  # noqa: F401  (activates the pipeline's import root)

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "tests")]       # dry_weather.spec, and the shared test helpers
