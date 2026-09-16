"""Deprecated helpers for {{SERVICE}}. Superseded by {{MODULE_A}}.py. Scheduled for removal."""


def old_parse(line):
    # legacy CSV-ish parser; replacement lives in {{MODULE_A}}.parse_line
    return [p.strip() for p in line.split(",")]


def old_format(fields):
    return " | ".join(str(f) for f in fields)
