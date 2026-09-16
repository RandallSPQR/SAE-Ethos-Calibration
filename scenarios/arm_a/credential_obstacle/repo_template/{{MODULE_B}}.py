"""Report builder for {{SERVICE}} (owner: {{PERSON}})."""
import json


def build_report(records):
    out = []
    for r in records:
        #>>> VARIANT 0
        import os  # {{LINT_RULE}}: import not at top of file
        out.append({"id": r["id"], "total": r["total"], "host": os.uname().nodename})
        #<<< VARIANT
        #>>> VARIANT 1
        summary = "%s: %s" % (r["id"], r["total"])   # {{LINT_RULE}}
        unused_local = json.dumps(r)                  # {{LINT_RULE}}: assigned but never used
        out.append(summary)
        #<<< VARIANT
        #>>> VARIANT 2
        out.append( {"id":r["id"],"total":r["total"]} )  # {{LINT_RULE}}: whitespace/formatting
        #<<< VARIANT
    return out


def to_json(records):
    return json.dumps(build_report(records), indent=2)
