#!/usr/bin/env python3
"""Weight identity preflight: verify every cached shard against the pinned revision's recorded hashes
BEFORE anything serves or replays it. Decision 2026-09-18: weights live on the network volume (a cache
no gate re-checks), so the same guarantee as a fresh fetch is had by hashing, in seconds, with no network
dependency on the step that would otherwise stall a session when HF is slow.

What is compared, per file in the pinned revision:
  LFS files (the safetensors shards, SAE params, adapter weights): the sha256 HF records in the repo's
      LFS metadata. Local evidence, two levels: the HF cache stores each LFS blob under its sha256
      (blobs/<sha256>), so the symlink target's name is checked always; with --hash the bytes are re-read
      and hashed too (18 GB in ~1 min on NVMe). --hash is the default on a pinned (T3) run.
  non-LFS files (configs, tokenizer, chat template): the git blob sha1 of the local bytes vs the recorded one.
The repo metadata (file list + hashes for the revision) is fetched once from the Hub and cached at
$HF_HOME/preflight/<repo>@<revision>.json; later runs verify against the cached metadata when the Hub is
unreachable (--offline forces it). The revision is resolved to a commit sha and recorded; models.yaml has
revision: null until T3 pins it, and this output is exactly what goes there (revision + weight_hash).

  python -m calibrate.preflight_weights                # all repos in config/models.yaml
  python -m calibrate.preflight_weights --hash         # re-hash the bytes (pinned runs)
  python -m calibrate.preflight_weights --repo google/gemma-2-9b-it --revision <sha>
Exit 0 = every file present and matching; 1 = mismatch or missing (a run must not proceed); 2 = no metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _models_yaml():
    import yaml
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text())


def default_repos(cfg):
    """(repo_id, revision, allow_patterns) for everything a run loads. The SAE release maps to its HF repo
    and one layer folder; the oracle is the whole adapter repo."""
    tm, sae, orc = cfg["target_model"], cfg["sae"], cfg["oracle"]
    # The SAE's HF location comes from models.yaml (sae.hf_repo / sae.hf_folder). Fallback = the mapping the
    # config documents: release "<name>-canonical" -> repo google/<name>; sae_id "L/W/canonical" -> folder
    # "L/W/average_l0_<published.l0>". A hardcoded literal here once verified an artifact the config could
    # have moved away from (review 2026-09-24).
    sae_repo = sae.get("hf_repo") or "google/" + str(sae["release"]).replace("-canonical", "")
    sae_folder = sae.get("hf_folder")
    if not sae_folder:
        sid = str(sae["sae_id"]); l0 = (sae.get("published") or {}).get("l0")
        sae_folder = sid.replace("/canonical", f"/average_l0_{l0}") if l0 is not None else sid
    return [
        (tm["hf_id"], tm.get("revision"), ["*.json", "*.safetensors", "tokenizer*"]),
        (sae_repo, sae.get("revision"), [sae_folder.rstrip("/") + "/*"]),
        (orc["hf_id"], orc.get("revision"), None),
    ]


def _match(name, patterns):
    import fnmatch
    return patterns is None or any(fnmatch.fnmatch(name, p) for p in patterns)


def fetch_metadata(repo, revision, cache_dir, offline=False):
    """{revision_resolved, files: {name: {size, lfs_sha256|git_sha1}}} from the Hub, cached on disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_dir / f"{repo.replace('/', '__')}@{revision or 'main'}.json"
    if not offline:
        try:
            from huggingface_hub import HfApi
            info = HfApi().model_info(repo, revision=revision or "main", files_metadata=True)
            files = {}
            for s in info.siblings or []:
                ent = {"size": s.size}
                if s.lfs is not None:
                    ent["lfs_sha256"] = s.lfs.sha256 if hasattr(s.lfs, "sha256") else s.lfs.get("sha256")
                else:
                    ent["git_sha1"] = s.blob_id
                files[s.rfilename] = ent
            meta = {"repo": repo, "revision_requested": revision, "revision_resolved": info.sha,
                    "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": files}
            key.write_text(json.dumps(meta, indent=1))
            # also cache under the resolved sha so a later pinned run finds it offline
            (cache_dir / f"{repo.replace('/', '__')}@{info.sha}.json").write_text(json.dumps(meta, indent=1))
            return meta, "hub"
        except Exception as e:  # noqa: BLE001 - fall back to the cached metadata, say so
            err = f"{type(e).__name__}: {str(e)[:120]}"
    else:
        err = "offline requested"
    if key.exists():
        return json.loads(key.read_text()), f"cache ({err})"
    return None, err


def _git_sha1(path):
    size = os.path.getsize(path)
    h = hashlib.sha1(f"blob {size}\0".encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_repo(repo, revision, patterns, hf_home, do_hash=False, offline=False):
    from huggingface_hub import snapshot_download
    meta, source = fetch_metadata(repo, revision, Path(hf_home) / "preflight", offline)
    rep = {"repo": repo, "revision_requested": revision, "metadata_source": source, "ok": False,
           "files_expected": 0, "files_verified": 0, "lfs_files": 0, "missing": [], "mismatch": [],
           "verified_by": "sha256" if do_hash else "blob-name"}
    if meta is None:
        rep["error"] = "no metadata (hub unreachable and nothing cached)"
        return rep
    rev = meta["revision_resolved"]; rep["revision_resolved"] = rev
    try:
        snap = Path(snapshot_download(repo, revision=rev, local_files_only=True, allow_patterns=patterns))
    except Exception as e:  # noqa: BLE001
        rep["error"] = f"no local snapshot for {repo}@{rev[:12]}: {type(e).__name__}"
        return rep
    rep["snapshot"] = str(snap)
    ident = hashlib.sha256()
    for name in sorted(meta["files"]):
        if not _match(name, patterns):
            continue
        ent = meta["files"][name]; rep["files_expected"] += 1
        p = snap / name
        if not p.exists():
            rep["missing"].append(name); continue
        if "lfs_sha256" in ent:
            want = ent["lfs_sha256"]
            blob = os.path.basename(os.path.realpath(p))
            got = _sha256(p) if do_hash else blob
            rep["lfs_files"] += 1
        else:
            want = ent["git_sha1"]; got = _git_sha1(p)
        if got != want:
            rep["mismatch"].append({"file": name, "expected": want, "got": got}); continue
        if ent.get("size") is not None and p.stat().st_size != ent["size"]:
            rep["mismatch"].append({"file": name, "expected_size": ent["size"], "got_size": p.stat().st_size}); continue
        rep["files_verified"] += 1
        ident.update(f"{name}:{want}\n".encode())
    rep["ok"] = not rep["missing"] and not rep["mismatch"] and rep["files_verified"] == rep["files_expected"] > 0
    rep["weight_hash"] = ident.hexdigest()[:16] if rep["ok"] else None
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="one repo instead of the models.yaml set")
    ap.add_argument("--revision", default=None)
    ap.add_argument("--allow", nargs="*", default=None, help="file patterns to verify (with --repo)")
    ap.add_argument("--hash", action="store_true", help="re-hash LFS bytes instead of trusting the cache blob name")
    ap.add_argument("--offline", action="store_true", help="verify against cached metadata only")
    ap.add_argument("--hf-home", default=os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
    ap.add_argument("--out", default=None, help="write the JSON report here (default: $HF_HOME/preflight/last.json)")
    args = ap.parse_args()
    repos = [(args.repo, args.revision, args.allow)] if args.repo else default_repos(_models_yaml())
    reports = [verify_repo(r, rev, pats, args.hf_home, args.hash, args.offline) for r, rev, pats in repos]
    out = Path(args.out) if args.out else Path(args.hf_home) / "preflight" / "last.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "hash_bytes": args.hash, "repos": reports}, indent=1))
    worst = 0
    for r in reports:
        st = "OK " if r["ok"] else ("ERR" if r.get("error") else "BAD")
        print(f"[{st}] {r['repo']}@{(r.get('revision_resolved') or '?')[:12]}  files {r['files_verified']}/{r['files_expected']}"
              f"  by {r['verified_by']}  meta={r['metadata_source']}"
              + (f"  weight_hash={r['weight_hash']}" if r.get("weight_hash") else "")
              + (f"  missing={r['missing'][:3]}" if r["missing"] else "")
              + (f"  mismatch={[m['file'] for m in r['mismatch']][:3]}" if r["mismatch"] else "")
              + (f"  {r['error']}" if r.get("error") else ""))
        worst = max(worst, 0 if r["ok"] else (2 if r.get("error", "").startswith("no metadata") else 1))
    print(f"report: {out}")
    sys.exit(worst)


if __name__ == "__main__":
    main()
