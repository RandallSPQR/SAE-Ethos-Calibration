"""Rebuild the tokenizer fixtures (llama-probe-fetch-tokenizers): the meta-llama tokenizer
and config files for the pinned revisions, fetched from ungated mirrors and accepted only if their git blob OID equals the
gated file's OID (fixtures/PROVENANCE.md). Text files only; no weights. Written to tokenizer_dir() (fixtures/tokenizers/
inside the package, or $LLAMA_PROBE_TOKENIZERS); files already there with the right OID are not fetched again."""
import hashlib
import sys
import urllib.request

from llama8b_it_base_probe.model_io import tokenizer_dir

FILES = {
    "Llama-3.1-8B-Instruct": {
        "tokenizer_config.json": ("db88166e2bc4c799fd5d1ae643b75e84d03ee70e", "hugging-quants/Meta-Llama-3.1-8B-Instruct-AWQ-INT4"),
        "tokenizer.json": ("5cc5f00a5b203e90a27a3bd60d1ec393b07971e8", "NousResearch/Meta-Llama-3.1-8B-Instruct"),
        "config.json": ("0bb6fd75b3ad2fe988565929f329945262c2814e", "NousResearch/Meta-Llama-3.1-8B-Instruct"),
        "generation_config.json": ("cc7276afd599de091142c6ed3005faf8a74aa257", "NousResearch/Meta-Llama-3.1-8B-Instruct"),
        "special_tokens_map.json": ("02ee80b6196926a5ad790a004d9efd6ab1ba6542", "NousResearch/Meta-Llama-3.1-8B-Instruct"),
    },
    "Llama-3.1-8B": {
        "tokenizer_config.json": ("cb9ec25536e44d86778b10509d3e5bdca459a5cf", "NousResearch/Meta-Llama-3.1-8B"),
        "tokenizer.json": ("f916e71031fa08f3c6ef1680a590c15b52d3cdd9", "NousResearch/Meta-Llama-3.1-8B"),
        "config.json": ("cccf055d6f8f210387a248c91dc40e0c7a4bafab", "NousResearch/Meta-Llama-3.1-8B"),
        "generation_config.json": ("fc9506438b7b55383dc04c0816561442324846c3", "NousResearch/Meta-Llama-3.1-8B"),
        "special_tokens_map.json": ("d8cd5076496dbe4be2320312abc10adc43097b81", "NousResearch/Meta-Llama-3.1-8B"),
    },
}


def blob_oid(b):
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def main():
    OUT = tokenizer_dir()
    bad = []
    for model, files in FILES.items():
        (OUT / model).mkdir(parents=True, exist_ok=True)
        for name, (oid, mirror) in files.items():
            dest = OUT / model / name
            if dest.exists() and blob_oid(dest.read_bytes()) == oid:
                continue
            with urllib.request.urlopen(f"https://huggingface.co/{mirror}/resolve/main/{name}", timeout=120) as r:
                b = r.read()
            if blob_oid(b) != oid:
                bad.append(f"{model}/{name} from {mirror}")
                continue
            dest.write_bytes(b)
    for b in bad:
        print("OID MISMATCH (not written):", b)
    print("ok" if not bad else f"{len(bad)} file(s) failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
