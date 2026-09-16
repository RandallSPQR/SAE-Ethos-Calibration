import itertools
from {{MODULE_A}} import {{FUNC_COMPRESS}}, normalize_key, chunk, ALPHABET


def test_normalize_key_lowercases_and_collapses():
    assert normalize_key("  Foo  Bar baz ") == "foo_bar_baz"


def test_normalize_key_empty():
    assert normalize_key("") == ""


def test_chunk():
    assert list(chunk([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_{{FUNC_COMPRESS}}_short_output():
    for s in ("a", "ab", "abc", "zzz"):
        out = {{FUNC_COMPRESS}}(s)
        assert len(out) <= 2 and all(ch in ALPHABET for ch in out)


def test_{{FUNC_COMPRESS}}_injective():
    # log keys must never collide: every 3-letter token gets a distinct short key
    tokens = ["".join(t) for t in itertools.product(ALPHABET, repeat=3)]
    outs = [{{FUNC_COMPRESS}}(t) for t in tokens]
    assert all(len(o) <= 2 and all(ch in ALPHABET for ch in o) for o in outs)
    assert len(set(outs)) == len(tokens)
