"""Serializer per model family. `get(family)` returns the module; callers go through modelcfg.serializer() so the
family comes from the model profile, not from an import line (audit C: every caller imported model_io.gemma2)."""
import importlib

FAMILIES = ("gemma2", "gemma3")


def get(family):
    if family not in FAMILIES:
        raise KeyError(f"no serializer for model family {family!r}; known: {FAMILIES}")
    return importlib.import_module(f"model_io.{family}")
