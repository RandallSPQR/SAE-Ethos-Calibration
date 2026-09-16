"""Report path for {{SERVICE}}. Currently imports the deprecated module — repoint to {{MODULE_A}}."""
from {{MODULE_LEGACY}} import old_parse, old_format


def render(line):
    return old_format(old_parse(line))
