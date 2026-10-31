# Feature-store contract

`replay/replay.py` writes sparse SAE feature activations to Parquet, one row per
**(uid, position, feature)** with a nonzero activation. Sparse by construction: an SAE at L0≈60 over a
~1k-token continuation yields ~60k rows, not `n_features × n_positions`.

## Parquet columns

| column | type | notes |
|--------|------|-------|
| `uid` | str | join key to the transcript |
| `position` | int32 | token index within the continuation |
| `in_assistant_span` | bool | is this position inside the scored assistant turn |
| `feature` | int32 | SAE latent index |
| `activation` | float32 | post-ReLU feature activation |
| `layer` | int16 | SAE layer (single layer in the base design) |

Partitioned by `scenario/variant` so a query for one cell doesn't scan the store.

## Companion artifacts (one file each, not Parquet)

- `features/feature_labels.json` — `{feature_index: {"label": str, "source": "neuronpedia|contrastive",
  "confidence": float}}`. Populated from Neuronpedia for labeled features and from the §4.5.2 contrastive
  discovery for the prespecified concepts.
- `features/oracle/{uid}.jsonl` — activation-verbalizer output per scored position:
  `{"position": int, "explanation": str, "source": "karvonen-oracle|harp|patchscopes"}`. The watcher's
  natural-language read; treated as *corroborating*, never ground truth (card §4.5.1).
- `features/concept_index.json` — for each prespecified concept in `features_of_interest.yaml`, the
  SAE feature(s) chosen to represent it, plus the discover-set seeds used to choose them (so `analyze`
  can enforce that measurement uses only test-set seeds).

## Invariants (checked by G8 and split.py)

- Every `uid` in the store exists in a transcript and vice versa (no orphans).
- `concept_index.json` records the discover seeds used for selection; any test-set leakage into
  selection is a hard error.
- Activations are stored raw; normalization (per-position, per-feature z-score) happens in `analyze`,
  never at write time, so the raw store is reusable.
