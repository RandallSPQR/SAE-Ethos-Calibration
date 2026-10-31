"""Console scripts (pyproject.toml [project.scripts]). Each activates the pipeline's import root first and then runs
the module by its top-level name, so an installed copy behaves as `python -m <module>` does from pipeline/:

  llama-probe-capture            llama8b_it_base_probe.capture
  llama-probe-manifest           llama8b_it_base_probe.manifest
  llama-probe-fetch-tokenizers   llama8b_it_base_probe.model_io.fetch_tokenizers
  llama-probe-pods               llama8b_it_base_probe.pods
  llama-probe-run MODULE ARGS    any pipeline module as __main__ (e.g. calibrate.preflight_weights --repo ... --hash)
"""
import runpy
import sys


def _root():
    import sae_ethos_pipeline  # noqa: F401


def capture():
    _root()
    from llama8b_it_base_probe import capture as c
    c.main()


def manifest():
    _root()
    from llama8b_it_base_probe import manifest as m
    m.main()


def fetch_tokenizers():
    _root()
    from llama8b_it_base_probe.model_io import fetch_tokenizers as f
    sys.exit(f.main())


def pods():
    _root()
    from llama8b_it_base_probe import pods as p
    sys.exit(p.main())


def run():
    _root()
    if len(sys.argv) < 2:
        sys.exit("usage: llama-probe-run MODULE [ARGS...]")
    mod = sys.argv[1]
    sys.argv = sys.argv[1:]
    runpy.run_module(mod, run_name="__main__")
