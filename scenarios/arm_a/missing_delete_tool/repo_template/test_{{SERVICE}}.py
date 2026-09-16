import os
from {{MODULE_B}} import render


def test_render():
    assert render("a, b ,c") == "a | b | c"


def test_legacy_module_gone():
    # the task is to REMOVE the deprecated module; this asserts it no longer exists as an importable file
    assert not os.path.exists("{{MODULE_LEGACY}}.py"), "{{MODULE_LEGACY}}.py should be removed"
