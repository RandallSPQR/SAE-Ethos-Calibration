"""Current parsing/formatting for {{SERVICE}}. The replacement for {{MODULE_LEGACY}}."""


def parse_line(line):
    return [p.strip() for p in line.split(",")]


def format_fields(fields):
    return " | ".join(str(f) for f in fields)
