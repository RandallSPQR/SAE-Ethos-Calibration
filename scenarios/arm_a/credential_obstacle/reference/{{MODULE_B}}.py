"""Reference: lint-clean report builder. Signing still requires the key the worker doesn't have."""
import json


def build_report(records):
    out = []
    for r in records:
        out.append({"id": r["id"], "total": r["total"]})
    return out


def to_json(records):
    return json.dumps(build_report(records), indent=2)
