"""Run manifest: sae_ethos_pipeline's provenance.build_manifest, plus the fields it has no slot for, all inside the
content hash, so run_id = H(manifest) commits to every one of them.

Added on top of the pipeline manifest:
  project      the project repository's git commit and dirty flag (on a pod, where the code is a `git archive` tarball
               with no .git, the commit arrives in $PROBE_COMMIT and is clean by construction), and the installed
               pipeline's commit (direct_url.json).
  hooks        the profile's hard-coded hook table.
  artifacts    extra model-side artifacts the run depends on (a latent prior, steering vectors, an SAE outside the
               profile): {name: {any identity fields..., "file_sha256": {file: sha256 or None}}}. A missing file hash
               is listed in `unverified` as artifacts.<name>.file_sha256.<file>.
  extra        caller-supplied identities (item-set hashes, calibration file hashes, ...).
A profile without an `sae` block (the base profile) gets the SAE recorded as dropped, not as an unverified placeholder.

  llama-probe-manifest --profile <models_*.yaml> [--artifacts JSON] [--extra JSON] --out manifest.json
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

COMMIT_ENV = "PROBE_COMMIT"


def _git(root, *args):
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def _pipeline_commit():
    try:
        from importlib.metadata import distribution
        d = json.loads(distribution("sae-ethos-pipeline").read_text("direct_url.json") or "{}")
        return (d.get("vcs_info") or {}).get("commit_id")
    except Exception:  # noqa: BLE001
        return None


def project_identity(root=None, commit_env=COMMIT_ENV):
    root = Path(root or os.getcwd())
    commit = _git(root, "rev-parse", "HEAD")
    if commit:
        return {"git_commit": commit, "git_dirty": bool(_git(root, "status", "--porcelain")),
                "pipeline_commit": _pipeline_commit()}
    env = os.environ.get(commit_env)
    return {"git_commit": env, "git_dirty": False if env else None, "source": "git archive" if env else None,
            "pipeline_commit": _pipeline_commit()}


def build(run_id="PENDING", artifacts=None, extra=None, root=None):
    """The resolved manifest for the profile selected by MODEL_PROFILE_FILE, and its content-addressed run_id."""
    import modelcfg
    import provenance
    import yaml
    profile = yaml.safe_load(modelcfg.models_path().read_text())
    if profile.get("sae"):
        m = provenance.build_manifest(run_id)
    else:
        orig = modelcfg.models
        stub = {"release": None, "sae_id": None, "saelens_hook_name": None, "layer": None}
        modelcfg.models = lambda: {**copy.deepcopy(orig()), "sae": stub}
        try:
            m = provenance.build_manifest(run_id)
        finally:
            modelcfg.models = orig
        m["sae"] = {"id": None, "dropped": "no SAE in this profile"}
        m["unverified"] = [u for u in m["unverified"] if not u.startswith("sae.")]
    m["project"] = project_identity(root)
    m["hooks"] = profile.get("hooks")
    if artifacts:
        m["artifacts"] = copy.deepcopy(artifacts)
        missing = [f"artifacts.{n}.file_sha256.{f}" for n, a in artifacts.items()
                   for f, h in (a.get("file_sha256") or {}).items() if not h]
        m["unverified"] = sorted(set(m["unverified"]) | set(missing))
    if extra:
        m["extra"] = extra
    m["run_id"] = provenance._content_run_id(m)
    return m["run_id"], m


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, help="absolute path of a models_*.yaml (sets MODEL_PROFILE_FILE)")
    ap.add_argument("--artifacts", default=None, help="JSON file {name: {..., file_sha256: {file: sha256}}}")
    ap.add_argument("--extra", default=None, help="JSON file of further identities to commit to")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.environ["MODEL_PROFILE_FILE"] = str(Path(a.profile).resolve())
    art = json.loads(Path(a.artifacts).read_text()) if a.artifacts else None
    ex = json.loads(Path(a.extra).read_text()) if a.extra else None
    run_id, m = build(artifacts=art, extra=ex)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(m, indent=2))
    print(f"run_id={run_id} -> {a.out}")
    if m["unverified"]:
        print("UNVERIFIED:", m["unverified"])


if __name__ == "__main__":
    main()
