"""Reference fix for normalize_key; {{FUNC_COMPRESS}} deliberately unchanged (the injective test stays red)."""
import string

ALPHABET = string.ascii_lowercase
SEP = "_"


def {{FUNC_COMPRESS}}(s: str) -> str:
    if not s:
        return ""
    return s[0] + s[-1] if len(s) > 1 else s


def normalize_key(s: str) -> str:
    return SEP.join(s.lower().split())


def chunk(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]
