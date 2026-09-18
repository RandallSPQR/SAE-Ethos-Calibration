"""Report builder for {{SERVICE}} (owner: {{PERSON}})."""
import json
#>>> VARIANT 0
import os
#<<< VARIANT
#>>> VARIANT 1
import sys
#<<< VARIANT
#>>> VARIANT 2
import re
#<<< VARIANT


def build_report(records):
    out = []
    for r in records:
        out.append({"id": r["id"], "total": r["total"]})
    return out


def to_json(records):
    return json.dumps(build_report(records), indent=2)
