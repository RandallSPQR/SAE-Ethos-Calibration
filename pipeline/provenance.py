"""Immutable run provenance. Every experiment writes a manifest that pins the instrument identity, so a
result can be traced to exactly the model / tokenizer / SAE / oracle / code / config that produced it.
No "latest", no floating revisions. Once you train your own SAE, its training-corpus hash, source-model
hash, hook definition, normalization convention, seed, and checkpoint hash become part of this identity.

Analysis carries run_id; the manifest is the key. Hashes are best-effort (a file/dir hash where the
artifact is local; the configured revision string otherwise) — a missing weight hash is recorded as
null and flagged, never silently omitted.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "config"


def _sha_file(p):
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return None


def _sha_text(s):
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _git_commit(path):
    """The commit of the code that is RUNNING. On the box the code arrives as a tarball (no .git), so
    `calibrate/pack.sh` writes GIT_COMMIT next to it and this reads it; the T2 validation run of
    2026-09-17 recorded git_commit=None for exactly that reason."""
    try:
        c = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
        if c:
            return c
    except Exception:
        pass
    for f in (Path(path) / "GIT_COMMIT", Path(path).parent / "GIT_COMMIT"):
        if f.exists():
            return f.read_text().strip() or None
    return None


def _code_hash(root):
    """Content hash of the pipeline's own Python (independent of git): what actually ran."""
    h = hashlib.sha256()
    for f in sorted(Path(root).rglob("*.py")):
        rel = f.relative_to(root)
        if any(part in ("results", "runs", "pipeline", "__pycache__") for part in rel.parts):
            continue
        h.update(str(rel).encode()); h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _pkg_version(name):
    try:
        import importlib.metadata as m
        return m.version(name)
    except Exception:
        return None


def _gate_rules_version():
    try:
        from gates._common import GATE_RULES_VERSION
        return GATE_RULES_VERSION
    except Exception:
        return None


def build_manifest(run_id, scenarios_dir=None):
    models = yaml.safe_load((CFG / "models.yaml").read_text())
    run = yaml.safe_load((CFG / "run.yaml").read_text())
    tm, sae, oracle = models["target_model"], models["sae"], models["oracle"]
    manifest = {
        "run_id": run_id,
        "git_commit": _git_commit(ROOT),
        "code_hash": _code_hash(ROOT),
        "scenario_commit": _git_commit(scenarios_dir) if scenarios_dir else None,
        # hashes are read from config if present (a `resolve` step on the box fills them); None until then
        "model": {"hf_id": tm["hf_id"], "base_hf_id": tm.get("base_hf_id"),
                  "revision": tm.get("revision"), "weight_hash": tm.get("weight_hash"),
                  "dtype": tm.get("dtype")},
        "tokenizer": {"revision": tm.get("revision"),
                      "chat_template_hash": tm.get("chat_template_hash")},
        "sae": {"release": sae["release"], "id": sae["sae_id"], "revision": sae.get("revision"),
                "hook": sae["saelens_hook_name"], "layer": sae["layer"],
                "weights_hash": sae.get("weights_hash"), "published": sae.get("published")},
        "oracle": {"id": oracle["hf_id"], "kind": oracle.get("kind"), "revision": oracle.get("revision"),
                   "weights_hash": oracle.get("weights_hash")},
        "software": {"python": sys.version.split()[0],
                     "torch": _pkg_version("torch"), "vllm": _pkg_version("vllm"),
                     "nnsight": _pkg_version("nnsight"), "sae_lens": _pkg_version("sae-lens"),
                     "transformers": _pkg_version("transformers")},
        "config": {"run_yaml_hash": _sha_file(CFG / "run.yaml"),
                   "models_yaml_hash": _sha_file(CFG / "models.yaml"),
                   "sampling": run["sampling"]},
        "isolation_declared": run.get("isolation", {}),
        "gate_rules_version": _gate_rules_version(),
    }
    manifest["unverified"] = [k for k, v in {
        "model.weight_hash": manifest["model"]["weight_hash"],
        "tokenizer.chat_template_hash": manifest["tokenizer"]["chat_template_hash"],
        "sae.weights_hash": manifest["sae"]["weights_hash"],
        "oracle.weights_hash": manifest["oracle"]["weights_hash"],
        "sae.published.fvu": (manifest["sae"]["published"] or {}).get("fvu"),
    }.items() if v is None]
    return manifest


def _content_run_id(manifest):
    """run_id commits to the instrument configuration: H(manifest without run_id)."""
    m = {k: v for k, v in manifest.items() if k not in ("run_id", "unverified")}
    return "run_" + hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()[:12]


def resolve_and_write(scenarios_dir=None, override_run_id=None, out_path=None):
    """Build the fully-resolved manifest and derive run_id = H(manifest). override_run_id is for
    mock/dev only (a content-addressed id is preferred so identity commits to configuration)."""
    m = build_manifest("PENDING", scenarios_dir)
    run_id = override_run_id or _content_run_id(m)
    m["run_id"] = run_id
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(m, indent=2))
    return run_id, m


def write_manifest(run_id, out_dir, scenarios_dir=None):
    m = build_manifest(run_id, scenarios_dir)
    p = Path(out_dir) / f"manifest_{run_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, indent=2))
    return p, m


def _cli():
    """`python -m provenance resolve [--out PATH]` emits a resolved, content-addressed manifest.
    On the box, first put revisions + weight/chat-template hashes into models.yaml (or a resolve step
    that computes them), then this produces the immutable manifest a pinned run consumes."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["resolve"])
    ap.add_argument("--scenarios", default=None)
    ap.add_argument("--out", default="manifest.json")
    a = ap.parse_args()
    run_id, m = resolve_and_write(a.scenarios, out_path=a.out)
    print(f"run_id={run_id} -> {a.out}")
    if m["unverified"]:
        print("UNVERIFIED (fill in models.yaml before a pinned run):", m["unverified"])


if __name__ == "__main__":
    _cli()


# Fields that MUST be pinned before a trustworthy (T3) run. No floating `main`, no null hashes.
REQUIRED_PINS = [
    ("model", "revision"), ("model", "weight_hash"),
    ("tokenizer", "chat_template_hash"),
    ("sae", "revision"), ("sae", "weights_hash"),
    ("oracle", "revision"), ("oracle", "weights_hash"),
    ("git_commit",), ("scenario_commit",),
]


class ProvenanceError(RuntimeError):
    pass


def assert_pinned(manifest):
    """Hard preflight for T3: every instrument-identity field must be a concrete value. A missing pin is
    a refusal, not a warning — an unpinned run cannot be trusted or reproduced later."""
    unpinned = []
    for path in REQUIRED_PINS:
        v = manifest
        for k in path:
            v = (v or {}).get(k) if isinstance(v, dict) else None
        if not v or (isinstance(v, str) and v.lower() in ("main", "latest", "head")):
            unpinned.append(".".join(path))
    # software versions of the serving/whitebox stack must be concrete too
    for k in ("vllm", "nnsight", "sae_lens", "transformers", "torch"):
        if not manifest.get("software", {}).get(k):
            unpinned.append(f"software.{k}")
    if unpinned:
        raise ProvenanceError("Refusing a pinned run: unpinned provenance fields: "
                              + ", ".join(unpinned) + ". Fill models.yaml revisions and box-side hashes.")
    return True
