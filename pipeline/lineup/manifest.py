"""The identity block every StPT / Missing Piece run manifest embeds (lineup-identity-block NAME).

It names: the pipeline (distribution version; git tag and commit, read from the checkout or from pip's direct_url.json
when installed from git); the lineup file's sha256; the model's pinned identities and its profile's sha256; the
serializer family and the sha256 of its source file; the serving stack pins and determinism flags. A study's manifest
compares `pipeline.commit` with the commit it pins and refuses to run on a mismatch (check_pin)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import lineup


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pipeline_source() -> dict:
    """{version, commit, tag, dirty, how}: from a git checkout of the pipeline if there is one, else from the
    installed distribution's PEP 610 direct_url.json (pip install git+...@v1.2.0 records the resolved commit)."""
    import sae_ethos_pipeline
    out = {"version": sae_ethos_pipeline.__version__, "commit": None, "tag": None, "dirty": None, "how": None}
    pkg = Path(sae_ethos_pipeline.__file__).resolve().parent
    root = pkg.parent
    # only a real checkout of this repository counts: an installed copy under site-packages may sit inside some OTHER
    # git repository (a study repo's .venv), whose commit must never be reported as the pipeline's
    pyproj = root / "pyproject.toml"
    is_checkout = pkg.name == "pipeline" and pyproj.exists() and 'name = "sae-ethos-pipeline"' in pyproj.read_text()
    try:
        if not is_checkout:
            raise FileNotFoundError("not a pipeline checkout")
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True).stdout.strip()
        tags = subprocess.run(["git", "-C", str(root), "tag", "--points-at", "HEAD"], capture_output=True,
                              text=True).stdout.split()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "pipeline", "pyproject.toml"],
                               capture_output=True, text=True).stdout.strip() != ""
        out.update(commit=commit, tag=tags[0] if tags else None, dirty=dirty, how="git checkout")
        return out
    except Exception:  # noqa: BLE001
        pass
    try:
        from importlib.metadata import distribution
        du = json.loads(distribution("sae-ethos-pipeline").read_text("direct_url.json") or "{}")
        vcs = du.get("vcs_info") or {}
        out.update(commit=vcs.get("commit_id"), tag=vcs.get("requested_revision"), dirty=False,
                   how="direct_url.json")
    except Exception:  # noqa: BLE001
        out["how"] = "unknown"
    return out


def identity_block(name: str) -> dict:
    import sae_ethos_pipeline  # noqa: F401
    import model_io
    e = lineup.models()[name]
    ly = lineup.lineup()
    ser = model_io.get(e["family"])
    return {
        "pipeline": pipeline_source(),
        "lineup": {"version": ly["lineup_version"], "locked": ly["locked"], "sha256": _sha(lineup.LINEUP_FILE)},
        "model": {k: e[k] for k in ("name", "hf_id", "revision", "weight_hash", "chat_template_hash", "family",
                                     "identity_status")},
        "profile": {"file": e["profile"], "sha256": _sha(lineup.CONFIG / e["profile"])},
        "serializer": {"family": ser.FAMILY, "module": ser.__name__, "sha256": _sha(ser.__file__),
                       "generation_prompt": ser.GENERATION_PROMPT},
        "serving": {"pins": ly["serving"]["pins"], "flags": ly["serving"]["determinism"]["flags"],
                    "env": ly["serving"]["determinism"]["env"], "batch_size": ly["serving"]["batch_size"]},
    }


def check_pin(block: dict, tag: str, commit: str) -> None:
    """Refuse a run whose installed pipeline is not the pinned tag + commit."""
    p = block["pipeline"]
    if p.get("commit") != commit:
        raise RuntimeError(f"pipeline commit {p.get('commit')} != pinned {commit} (tag {tag})")
    if p.get("dirty"):
        raise RuntimeError("pipeline checkout has local changes; a pinned run needs the tagged tree")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in lineup.models():
        print(f"usage: lineup-identity-block NAME   (NAME in {list(lineup.models())})", file=sys.stderr)
        return 2
    print(json.dumps(identity_block(argv[0]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
