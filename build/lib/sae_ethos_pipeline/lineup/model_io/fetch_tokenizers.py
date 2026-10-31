"""Rebuild the Qwen3.5 tokenizer fixtures (lineup-fetch-tokenizers): tokenizer and config files at the PINNED revision,
fetched from the Qwen repositories themselves (public, Apache-2.0) and accepted only if they match the revision's
recorded identity: git blob OID for regular files, LFS sha256 for tokenizer.json (fixtures/PROVENANCE.md). Text files
only; no weights. The tokenizer files are byte-identical across the 9B and 27B revisions, so one directory serves both;
config.json and generation_config.json differ and are kept per model. Written to tokenizer_dir() (gitignored)."""
import hashlib
import sys
import urllib.request

from lineup.model_io import tokenizer_dir

REVISIONS = {"Qwen/Qwen3.5-9B": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
             "Qwen/Qwen3.5-27B": "fc05daec18b0a78c049392ed2e771dde82bdf654"}
# (directory, file) -> (repo, kind, identity)
FILES = {
    ("Qwen3.5", "tokenizer_config.json"): ("Qwen/Qwen3.5-9B", "git", "eda48d3e75a8e59a8479ee4ec8b37f76e711d9c1"),
    ("Qwen3.5", "tokenizer.json"): ("Qwen/Qwen3.5-9B", "lfs", "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"),
    ("Qwen3.5", "vocab.json"): ("Qwen/Qwen3.5-9B", "git", "0aa0ce0658d60ac4a5d609f4eadb0e8e43514176"),
    ("Qwen3.5", "merges.txt"): ("Qwen/Qwen3.5-9B", "git", "a494e019ca1502219fd0128658b979e5f05ae8e8"),
    ("Qwen3.5", "chat_template.jinja"): ("Qwen/Qwen3.5-9B", "git", "a585dec894e63da457d9440ec6aa7caa16d20860"),
    ("Qwen3.5-9B", "config.json"): ("Qwen/Qwen3.5-9B", "git", "273ce437e01baf96a07cd9eb3d5f48bac8d7c657"),
    ("Qwen3.5-27B", "config.json"): ("Qwen/Qwen3.5-27B", "git", "bde2be7f34a16fcdf78b9050319cdb330a15fecd"),
    ("Qwen3.5-27B", "generation_config.json"): ("Qwen/Qwen3.5-27B", "git", "85b45ab4f3a24f95a061c5260559471a259187cc"),
}


def blob_oid(b):
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def identity(kind, b):
    return hashlib.sha256(b).hexdigest() if kind == "lfs" else blob_oid(b)


def main():
    out = tokenizer_dir()
    bad = []
    for (d, name), (repo, kind, want) in FILES.items():
        dest = out / d / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and identity(kind, dest.read_bytes()) == want:
            continue
        url = f"https://huggingface.co/{repo}/resolve/{REVISIONS[repo]}/{name}"
        with urllib.request.urlopen(url, timeout=120) as r:
            b = r.read()
        if identity(kind, b) != want:
            bad.append(f"{d}/{name} from {repo}")
            continue
        dest.write_bytes(b)
    for b in bad:
        print("IDENTITY MISMATCH (not written):", b)
    print("ok" if not bad else f"{len(bad)} file(s) failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
