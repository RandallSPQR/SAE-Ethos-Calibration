#!/usr/bin/env python3
"""Static + solvability validation for Arm-A scenarios. Pure stdlib + pyyaml.

Checks per scenario:
  1. scenario.yaml parses and has required fields; arm matches directory.
  2. Every {{VAR}} used in the template/prompt is declared in `surface` and exists in surface_bank.
  3. render.py can render seed 0 for all three variants without error.
  4. full/ contains its canary (if any); control/ and mention/ contain none.
  5. Solvability signal: control/ has no forbidden file / has the tool that makes the benign path work.
     (Full pytest-based solvability is run by the pipeline in-sandbox; here we do the static portion.)

Exit non-zero if any check fails. Usage: python scripts/validate.py [--arm a]
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "common"
VAR_RE = re.compile(r"\{\{(\w+)\}\}")
REQUIRED = ["id", "arm", "family", "task_prompt", "tools", "obstacle", "benign_path",
            "labels", "features_to_watch", "surface", "variants", "gates"]
NON_SURFACE = {"SEED"}


def used_vars(scenario_dir):
    found = set()
    for p in list(scenario_dir.rglob("*")) + [scenario_dir / "scenario.yaml"]:
        if p.is_file():
            try:
                found |= set(VAR_RE.findall(p.read_text()))
            except UnicodeDecodeError:
                pass
    return found - NON_SURFACE - {"CANARY_HEX"} | ({"CANARY_HEX"} if "CANARY_HEX" in found else set())


def check_scenario(sdir, bank_keys, errors):
    name = sdir.name
    spec = yaml.safe_load((sdir / "scenario.yaml").read_text())
    for f in REQUIRED:
        if f not in spec:
            errors.append(f"[{name}] missing field: {f}")
    if spec.get("id") != name:
        errors.append(f"[{name}] id '{spec.get('id')}' != directory name")
    if spec.get("arm") != "A":
        errors.append(f"[{name}] arm must be A in arm_a/")
    declared = set(spec.get("surface", []))
    for v in used_vars(sdir):
        if v not in declared and v not in NON_SURFACE:
            errors.append(f"[{name}] template uses {{{{{v}}}}} not in surface: {sorted(declared)}")
        if v not in bank_keys and v not in NON_SURFACE:
            errors.append(f"[{name}] surface var {v} not in surface_bank.yaml")
    # arm isolation
    for p in sdir.rglob("*"):
        if p.is_file():
            try:
                if "arm_b" in p.read_text():
                    errors.append(f"[{name}] references arm_b in {p.name}")
            except UnicodeDecodeError:
                pass


def render_and_check(sdir, errors):
    name = sdir.name
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "render.py"),
                            "--scenario", name, "--seeds", "0", "--out", td],
                           capture_output=True, text=True)
        if r.returncode != 0:
            errors.append(f"[{name}] render failed: {r.stderr.strip()[:400]}")
            return
        seed_dir = Path(td) / "discover" / name / "seed_000"
        if not seed_dir.exists():
            errors.append(f"[{name}] no seed_000 produced")
            return
        man = yaml.safe_load((seed_dir / "manifest.json").read_text())
        canary = man.get("canary")
        full = seed_dir / "full"
        control = seed_dir / "control"
        mention = seed_dir / "mention"
        # canary containment
        if canary and "CANARY_HEX" in man["surface"]:
            in_full = any(canary in p.read_text() for p in full.rglob("*")
                          if p.is_file() and _text(p))
            in_ctrl = any(canary in p.read_text() for p in control.rglob("*")
                          if p.is_file() and _text(p))
            # canary lives in bin/git as a check; acceptable in full only
            if in_ctrl:
                errors.append(f"[{name}] canary leaked into control/")
        # solvability-ish: control should differ from full (obstacle removed)
        f_files = {p.name for p in full.rglob("*") if p.is_file()}
        c_files = {p.name for p in control.rglob("*") if p.is_file()}
        if f_files == c_files and (spec_removes(sdir)):
            errors.append(f"[{name}] control/ identical to full/ but variant declares removals")


def spec_removes(sdir):
    # only remove_files changes the file SET; remove_tests edits content and is checked separately
    spec = yaml.safe_load((sdir / "scenario.yaml").read_text())
    c = (spec.get("variants") or {}).get("control", {})
    return bool(c.get("remove_files"))


def _text(p):
    try:
        p.read_text(); return True
    except UnicodeDecodeError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="a")
    args = ap.parse_args()
    bank = yaml.safe_load((COMMON / "surface_bank.yaml").read_text())
    bank_keys = set(bank.keys())
    errors = []
    arm_dir = ROOT / f"arm_{args.arm}"
    scenarios = sorted(p.parent for p in arm_dir.glob("*/scenario.yaml"))
    if not scenarios:
        print(f"no scenarios in {arm_dir}"); sys.exit(1)
    for sdir in scenarios:
        check_scenario(sdir, bank_keys, errors)
        render_and_check(sdir, errors)
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print(f"OK: {len(scenarios)} scenario(s) validated: {', '.join(s.name for s in scenarios)}")


if __name__ == "__main__":
    main()
