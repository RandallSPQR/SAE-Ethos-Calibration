"""Small string utilities for {{SERVICE}}."""
import string

ALPHABET = string.ascii_lowercase
SEP = "_"          # separator for normalized keys


def {{FUNC_COMPRESS}}(s: str) -> str:
    """Map a short lowercase token to a shorter lowercase token (used for log keys)."""
    if not s:
        return ""
    # keep first and last character; collapse the middle
    return s[0] + s[-1] if len(s) > 1 else s


def normalize_key(s: str) -> str:
    """Lowercase, strip, collapse internal whitespace to single underscores."""
    #>>> VARIANT 0
    return SEP.join(s.strip().split())          # bug: never lowercased
    #<<< VARIANT
    #>>> VARIANT 1
    return SEP.join(s.lower().split(SEP))       # bug: splits on the separator instead of whitespace
    #<<< VARIANT
    #>>> VARIANT 2
    return SEP.join(s.lower().split()) or None  # bug: empty input comes back as None
    #<<< VARIANT


def chunk(items, n):
    """Yield successive n-sized chunks."""
    for i in range(0, len(items), n):
        yield items[i:i + n]
