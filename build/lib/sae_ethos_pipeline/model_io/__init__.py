"""Serializer per model family: the ONLY place a model family's prompt format lives. `get(family)` returns the module;
callers go through modelcfg.serializer() so the family comes from the model profile, not from an import line (audit C:
every caller imported model_io.gemma2).

An adapter is a module with:
  FAMILY             the family name the profile's `family:` field names
  END_OF_TURN        the string that closes a turn ("<end_of_turn>" for Gemma)
  TURN_SUFFIX        what closes a model turn in the serialized string (replay's span excludes it)
  GENERATION_PROMPT  what a generation prompt ends with (the opening of the model's turn)
  user_turn(text)    one user turn in the family's markup, without <bos> or a generation prompt
  serialize_messages(messages, add_generation_prompt=True) -> str        the canonical prompt string
  apply_to_tokenizer(tokenizer, messages, add_generation_prompt=True) -> list[int]
  prompt_hash(messages) -> str

Built-in: gemma2 (Gemma-2-9B-IT), gemma3 (Gemma-3-27B-IT). Another package adds a family without editing this repo,
either by `register("llama3", module)` before use, or by an entry point in its own pyproject.toml:
  [project.entry-points."sae_ethos_pipeline.model_io"]
  llama3 = "my_pkg.llama3_adapter"
"""
import importlib

FAMILIES = ("gemma2", "gemma3")
REQUIRED = ("FAMILY", "END_OF_TURN", "TURN_SUFFIX", "GENERATION_PROMPT", "user_turn", "serialize_messages",
            "apply_to_tokenizer", "prompt_hash")
ENTRY_POINT_GROUP = "sae_ethos_pipeline.model_io"
_REGISTRY = {}


def check_adapter(module):
    missing = [a for a in REQUIRED if not hasattr(module, a)]
    if missing:
        raise TypeError(f"model_io adapter {getattr(module, '__name__', module)!r} lacks {missing}")
    return module


def register(family, module):
    """Add (or replace) the adapter for `family`. `module` is a module object or an importable dotted name."""
    _REGISTRY[family] = check_adapter(importlib.import_module(module) if isinstance(module, str) else module)


def _entry_point(family):
    try:
        from importlib.metadata import entry_points
    except ImportError:                                   # pragma: no cover
        return None
    eps = entry_points()
    group = eps.select(group=ENTRY_POINT_GROUP) if hasattr(eps, "select") else eps.get(ENTRY_POINT_GROUP, [])
    for ep in group:
        if ep.name == family:
            return ep.load()
    return None


def known():
    return tuple(sorted(set(FAMILIES) | set(_REGISTRY)))


def get(family):
    if family in _REGISTRY:
        return _REGISTRY[family]
    if family in FAMILIES:
        return importlib.import_module(f"model_io.{family}")
    mod = _entry_point(family)
    if mod is not None:
        register(family, mod)
        return _REGISTRY[family]
    raise KeyError(f"no serializer for model family {family!r}; known: {known()} (add one with model_io.register "
                   f"or the {ENTRY_POINT_GROUP!r} entry point)")
