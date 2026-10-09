"""sae_ethos_pipeline: the pipeline/ directory of SAE-Ethos-Calibration as an installable package (RELEASE_NOTES.md).

The pipeline's modules import each other by top-level name (`import modelcfg`, `from harness.run_harness import ...`),
because on a pod they run from the pipeline/ directory (`python -m harness.run_item10`). Importing this package puts
its own directory on sys.path, so the same imports work from an installed copy:

    import sae_ethos_pipeline                  # activates the pipeline's import root
    import provenance, modelcfg                # then the modules, by the names the code itself uses
    from gates import run_gates
    from model_io import register              # add a model family from outside (see model_io/__init__.py)

Use those top-level names, not `sae_ethos_pipeline.gates`: the pipeline's internal imports refer to `gates`, and two
names for one file would load it twice.
"""
import sys
from pathlib import Path

__version__ = "1.0.0"
ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
