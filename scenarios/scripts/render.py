#!/usr/bin/env python3
"""Render Arm-A scenario templates into concrete per-seed repos (full/control/mention), with the
discover/test seed split. Deterministic from seed. Pure stdlib.

Usage:
  python scripts/render.py --arm a --seeds 0-99 --out build/
  python scripts/render.py --scenario honeypot_forbidden_table --seeds 0-3 --out build/

Layout produced:
  build/{discover|test}/<scenario>/seed_<NNN>/{full,control,mention}/   rendered repo
  build/{...}/seed_<NNN>/_side/                                          out-of-repo answers (answer.txt)
  build/{...}/seed_<NNN>/manifest.json                                  surface values, canaries, answer
"""
import argparse
import hashlib
import importlib.util
import json
import random
import re
import shutil
from pathlib import Path

import yaml  # PyYAML; if absent, pip install pyyaml --break-system-packages

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "common"
VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def load_surface_bank():
    return yaml.safe_load((COMMON / "surface_bank.yaml").read_text())


def seed_rng(scenario_id, seed):
    h = hashlib.sha256(f"{scenario_id}:{seed}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def pick_surface(keys, bank, rng, seed, scenario_id):
    out = {}
    for k in keys:
        if k == "CANARY_HEX":
            out[k] = hashlib.sha256(f"{scenario_id}:{seed}:canary".encode()).hexdigest()[:8]
        elif k == "BUG_VARIANT":
            out[k] = str(rng.choice(bank["BUG_VARIANT"]))
        else:
            out[k] = rng.choice(bank[k])
    out["SEED"] = str(seed)
    return out


def subst(text, surface):
    return VAR_RE.sub(lambda m: str(surface.get(m.group(1), m.group(0))), text)


def strip_variants(text, keep_index):
    """Resolve #>>> VARIANT n ... #<<< VARIANT blocks, keeping only block `keep_index`."""
    lines = text.splitlines(keepends=True)
    out, skip, depth_keep = [], False, None
    i = 0
    cur = None
    for line in lines:
        s = line.strip()
        if s.startswith("#>>> VARIANT"):
            cur = int(s.split()[-1])
            skip = (cur != keep_index)
            continue
        if s.startswith("#<<< VARIANT"):
            cur, skip = None, False
            continue
        if not skip:
            out.append(line)
    return "".join(out)


def render_tree(template_dir, dest, surface, variant_index):
    for src in sorted(Path(template_dir).rglob("*")):
        rel = subst(str(src.relative_to(template_dir)), surface)
        target = dest / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = src.read_text()
        raw = strip_variants(raw, variant_index) if "#>>> VARIANT" in raw else raw
        target.write_text(subst(raw, surface))
        if src.name == "git" and "bin" in str(src):
            target.chmod(0o755)


def apply_variant_edits(spec, variant, repo_dir, surface):
    """Very small subset: file removal, readme block strip/keep, tool add. Structural edits that need
    a real AST (repoint_imports, make_stub) are left to the pipeline's apply step; here we mark them."""
    v = (spec.get("variants") or {}).get(variant, {})
    for f in v.get("remove_files", []) or []:
        p = repo_dir / subst(f, surface)
        if p.exists():
            p.unlink()
    for t in v.get("remove_tests", []) or []:
        _strip_test(repo_dir, subst(t, surface))
    # readme block handling
    readme = repo_dir / "README.md"
    if readme.exists():
        txt = readme.read_text()
        if "strip_readme_block" in v:
            txt = _drop_block(txt, v["strip_readme_block"])
        if v.get("add_readme_block"):
            txt += "\n" + subst(v["add_readme_block"], surface) + "\n"
        readme.write_text(txt)
    # named blocks in arbitrary files (same <!-- NAME --> ... <!-- /NAME --> markers as README)
    for fb in v.get("strip_file_blocks", []) or []:
        fp = repo_dir / subst(fb["file"], surface)
        if fp.exists():
            fp.write_text(_drop_block(fp.read_text(), fb["block"]))
    # note declarative-only ops for the pipeline
    notes = {k: v[k] for k in ("add_tool", "env_signing_key", "add_field") if k in v}
    return notes


def _drop_block(txt, name):
    return re.sub(rf"<!-- {name} -->.*?<!-- /{name} -->", "", txt, flags=re.S).strip() + "\n"


def _strip_test(repo_dir, test_name):
    for p in repo_dir.rglob("test_*.py"):
        src = p.read_text()
        pat = re.compile(rf"\ndef {re.escape(test_name)}\(.*?(?=\ndef |\Z)", re.S)
        new = pat.sub("\n", src)
        if new != src:
            p.write_text(new)


def maybe_generate(scenario_dir, repo_dir, side_dir, surface, rng):
    gen = scenario_dir / "generate.py"
    if not gen.exists():
        return {}
    spec = importlib.util.spec_from_file_location("gen", gen)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.generate(str(repo_dir), str(side_dir), surface, rng) or {}


def render_scenario(scenario_dir, seeds, out_root, bank):
    spec = yaml.safe_load((scenario_dir / "scenario.yaml").read_text())
    sid = spec["id"]
    for seed in seeds:
        split = "discover" if seed <= 49 else "test"
        rng = seed_rng(sid, seed)
        surface = pick_surface(spec.get("surface", []), bank, rng, seed, sid)
        var_index = int(surface.get("BUG_VARIANT", 0))
        seed_dir = out_root / split / sid / f"seed_{seed:03d}"
        side_dir = seed_dir / "_side"
        for variant in ("full", "control", "mention"):
            dest = seed_dir / variant
            if dest.exists():
                shutil.rmtree(dest)
            render_tree(scenario_dir / "repo_template", dest, surface, var_index)
            genmeta = maybe_generate(scenario_dir, dest, side_dir, surface, seed_rng(sid, seed))
            notes = apply_variant_edits(spec, variant, dest, surface)
        manifest = {"scenario": sid, "arm": spec["arm"], "seed": seed, "split": split,
                    "surface": surface, "variant_index": var_index,
                    "canary": f"CANARY-{sid}-{seed}-{surface.get('CANARY_HEX','')}"
                              if "CANARY_HEX" in surface else None,
                    "gen": genmeta, "declared_ops": notes}
        (seed_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return sid, len(seeds)


def parse_seeds(s):
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="a")
    ap.add_argument("--scenario")
    ap.add_argument("--seeds", default="0-3")
    ap.add_argument("--out", default="build")
    args = ap.parse_args()
    bank = load_surface_bank()
    seeds = parse_seeds(args.seeds)
    out_root = Path(args.out)
    arm_dir = ROOT / f"arm_{args.arm}"
    scenarios = ([arm_dir / args.scenario] if args.scenario
                 else [p.parent for p in arm_dir.glob("*/scenario.yaml")])
    for sdir in scenarios:
        sid, n = render_scenario(sdir, seeds, out_root, bank)
        print(f"rendered {sid}: {n} seeds -> {out_root}")


if __name__ == "__main__":
    main()
