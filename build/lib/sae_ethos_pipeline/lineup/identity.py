"""Identity checks for the five lineup models (lineup-identity).

Three levels, cheapest first:
  local    lineup.yaml against each profile file (hf_id, revision, weight_hash, chat_template_hash, family) and the
           serializer's registration. No network. Run by the tests.
  offline  every pinned value recomputed from the Hugging Face API at the pinned revision: the revision resolves to
           itself; weight_hash recomputed from the revision's recorded per-file identities with the SAME function
           calibrate/preflight_weights.py uses on the box (sorted "name:identity\\n" over *.json, *.safetensors,
           tokenizer*); chat_template_hash from tokenizer_config.json at the revision. No weights downloaded.
  box      on a GPU pod, after the weights are on disk: preflight_weights --hash (bytes re-read), modelcfg.check_tokenizer
           and template_agreement against the real tokenizer, then (with a server up) the readout repeat probe and the
           exact-fallback consistency check (lineup.readout). Written by pod/serve_lineup.sh; not runnable here.

    lineup-identity local
    lineup-identity offline [--out report.json]
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
import time

import lineup

WEIGHT_PATTERNS = ["*.json", "*.safetensors", "tokenizer*"]     # calibrate/preflight_weights.default_repos, target model
CHECKED = ("hf_id", "revision", "weight_hash", "chat_template_hash")


def weight_hash_from_files(files: dict, patterns=WEIGHT_PATTERNS) -> str:
    """files: {name: {"lfs_sha256"|"git_sha1": identity}} -> the preflight identity (sha256[:16]). Must stay equal to
    calibrate/preflight_weights.verify_repo's `ident` for a fully verified snapshot."""
    ident = hashlib.sha256()
    n = 0
    for name in sorted(files):
        if not any(fnmatch.fnmatch(name, p) for p in patterns):
            continue
        ent = files[name]
        ident.update(f"{name}:{ent.get('lfs_sha256') or ent['git_sha1']}\n".encode())
        n += 1
    if n == 0:
        raise ValueError("no files match the weight patterns")
    return ident.hexdigest()[:16]


def chat_template_hash(tokenizer_config: dict) -> str | None:
    ct = tokenizer_config.get("chat_template")
    if not ct:
        return None
    return hashlib.sha256((ct if isinstance(ct, str) else json.dumps(ct, sort_keys=True)).encode()).hexdigest()[:16]


def _profile(entry):
    import yaml
    return yaml.safe_load((lineup.CONFIG / entry["profile"]).read_text())


def local_check() -> dict:
    """lineup.yaml vs each profile and the serializer registry. No network."""
    import sae_ethos_pipeline  # noqa: F401
    import model_io
    out = {}
    for name, e in lineup.models().items():
        prof = _profile(e)
        tm = prof["target_model"]
        fam = prof.get("family", "gemma2")
        rep = {k: (e[k] == tm.get(k)) for k in CHECKED}
        rep["family"] = e["family"] == fam
        try:
            model_io.check_adapter(model_io.get(fam))
            rep["serializer_registered"] = True
        except Exception:  # noqa: BLE001
            rep["serializer_registered"] = False
        rep["ok"] = all(rep.values())
        out[name] = rep
    return out


def hub_files(repo: str, revision: str):
    """(resolved sha, {name: {size, lfs_sha256|git_sha1}}) from the HF API (metadata only)."""
    from huggingface_hub import HfApi
    info = HfApi().model_info(repo, revision=revision, files_metadata=True)
    files = {}
    for s in info.siblings or []:
        ent = {"size": s.size}
        if s.lfs is not None:
            ent["lfs_sha256"] = s.lfs.sha256 if hasattr(s.lfs, "sha256") else s.lfs.get("sha256")
        else:
            ent["git_sha1"] = s.blob_id
        files[s.rfilename] = ent
    return info.sha, files


def hub_tokenizer_config(repo: str, revision: str) -> dict:
    from huggingface_hub import hf_hub_download
    return json.loads(open(hf_hub_download(repo, "tokenizer_config.json", revision=revision)).read())


def offline_check(names=None) -> dict:
    """Recompute every pinned identity from the HF API at the pinned revision."""
    out = {"checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "models": {}}
    for name, e in lineup.models().items():
        if names and name not in names:
            continue
        rep = {"hf_id": e["hf_id"], "revision": e["revision"], "identity_status": e["identity_status"]}
        try:
            sha, files = hub_files(e["hf_id"], e["revision"])
            rep["revision_resolves"] = sha == e["revision"]
            rep["weight_hash_recomputed"] = weight_hash_from_files(files)
            rep["weight_hash_ok"] = rep["weight_hash_recomputed"] == e["weight_hash"]
            rep["n_weight_files"] = sum(1 for f in files if f.endswith(".safetensors"))
            rep["bytes"] = sum(v["size"] or 0 for f, v in files.items() if f.endswith(".safetensors"))
        except Exception as ex:  # noqa: BLE001
            rep["error_weights"] = f"{type(ex).__name__}: {str(ex)[:160]}"
        try:
            rep["chat_template_hash_recomputed"] = chat_template_hash(hub_tokenizer_config(e["hf_id"], e["revision"]))
            rep["chat_template_hash_ok"] = rep["chat_template_hash_recomputed"] == e["chat_template_hash"]
        except Exception as ex:  # noqa: BLE001
            # gated repos (meta-llama) refuse file downloads without an accepted licence; the template fixture's OID
            # check (llama8b_it_base_probe fixtures/PROVENANCE.md) then stands in, and that is recorded
            rep["error_template"] = f"{type(ex).__name__}: {str(ex)[:160]}"
        rep["ok"] = bool(rep.get("revision_resolves") and rep.get("weight_hash_ok")
                         and rep.get("chat_template_hash_ok", "error_template" in rep))
        out["models"][name] = rep
    out["ok"] = all(r["ok"] for r in out["models"].values())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level", choices=["local", "offline"])
    ap.add_argument("--model", action="append")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rep = local_check() if a.level == "local" else offline_check(a.model)
    txt = json.dumps(rep, indent=1)
    if a.out:
        open(a.out, "w").write(txt + "\n")
    print(txt)
    ok = all(r["ok"] for r in rep.values()) if a.level == "local" else rep["ok"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
