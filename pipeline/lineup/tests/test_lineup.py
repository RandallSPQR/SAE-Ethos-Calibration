"""The lineup file, the profiles and the identity function, offline:
  - lineup.yaml agrees with every profile (local check) and names exactly the five models of both briefs;
  - the weight_hash function reproduces EVERY pinned weight_hash from the recorded HF metadata snapshot, including the
    three that were measured on pods by calibrate/preflight_weights.py --hash (so the Qwen pins, computed the same way,
    are the values the box check must reproduce);
  - it is the same function preflight_weights uses (checked against preflight's own code on a fake snapshot);
  - the serving command carries the determinism flags exactly once and serves multimodal checkpoints text-only;
  - the identity block names pipeline, lineup, model, profile, serializer and serving."""
import json
from pathlib import Path

import pytest
import yaml

import lineup
from lineup import identity, manifest, serve

SNAP = json.loads((Path(__file__).parent / "data" / "hf_identity_2026-10-31.json").read_text())


def test_five_models():
    assert list(lineup.models()) == ["llama-3.1-8b-it", "gemma-2-9b-it", "gemma-3-27b-it", "qwen3.5-9b", "qwen3.5-27b"]
    assert lineup.lineup()["locked"] is True and lineup.lineup()["lineup_version"] == lineup.version() == "1.3.0"


def test_local_check():
    rep = identity.local_check()
    assert all(r["ok"] for r in rep.values()), rep


@pytest.mark.parametrize("name", list(lineup.models()))
def test_weight_hash_reproduced_from_metadata(name):
    e = lineup.models()[name]
    snap = SNAP["models"][e["hf_id"]]
    assert snap["revision"] == e["revision"]
    assert identity.weight_hash_from_files(snap["files"]) == e["weight_hash"]


def test_same_function_as_preflight(tmp_path, monkeypatch):
    """preflight_weights.verify_repo on a fake local snapshot whose files carry known identities must give the same
    weight_hash as identity.weight_hash_from_files on the same metadata."""
    import hashlib
    import calibrate.preflight_weights as pw
    files = {"config.json": b'{"a": 1}', "model-00001.safetensors": b"\x00" * 64, "tokenizer.json": b"{}", "README.md": b"x"}
    snapdir = tmp_path / "snap"
    snapdir.mkdir()
    meta = {}
    for n, b in files.items():
        (snapdir / n).write_bytes(b)
        if n.endswith(".safetensors") or n == "tokenizer.json":
            meta[n] = {"size": len(b), "lfs_sha256": hashlib.sha256(b).hexdigest()}
        else:
            meta[n] = {"size": len(b), "git_sha1": hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()}
    monkeypatch.setattr(pw, "fetch_metadata", lambda *a, **k: ({"revision_resolved": "r", "files": meta}, "test"))
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **k: str(snapdir))
    rep = pw.verify_repo("x/y", "r", ["*.json", "*.safetensors", "tokenizer*"], str(tmp_path), do_hash=True)
    assert rep["ok"] and rep["weight_hash"] == identity.weight_hash_from_files(meta)


def test_chat_template_hash_convention():
    from lineup.model_io import FIXTURES
    t = (FIXTURES / "qwen3.5_chat_template.jinja").read_text()
    assert identity.chat_template_hash({"chat_template": t}) == "a4aee8afcf2e0711"


@pytest.mark.parametrize("name", list(lineup.models()))
def test_serve_command(name):
    argv = serve.vllm_argv(name, python="py")
    flags = [a for a in argv if a.startswith("--")]
    assert len(flags) == len(set(flags)), flags
    for f in ("--max-num-seqs", "--no-enable-prefix-caching", "--logprobs-mode", "--seed", "--dtype", "--revision"):
        assert f in argv
    assert argv[argv.index("--max-num-seqs") + 1] == "1"
    assert argv[argv.index("--logprobs-mode") + 1] == "raw_logprobs"
    assert argv[argv.index("--revision") + 1] == lineup.models()[name]["revision"]
    prof = yaml.safe_load(lineup.profile_path(name).read_text())
    mm = (prof.get("replay") or {}).get("hf_auto_class") == "AutoModelForImageTextToText"
    assert ("--limit-mm-per-prompt.image" in argv) == mm == ("--limit-mm-per-prompt.video" in argv)
    if mm:
        assert argv[argv.index("--limit-mm-per-prompt.image") + 1] == argv[argv.index("--limit-mm-per-prompt.video") + 1] == "0"
    # model_env.sh's unquoted expansion of the profile's extra_args must give the same words as shlex
    import shlex, subprocess
    ex = (prof.get("serve") or {}).get("extra_args") or ""
    words = subprocess.run(["bash", "-c", 'printf "%s\\n" $X'], env={"X": ex}, capture_output=True, text=True).stdout.split("\n")[:-1]
    assert [w for w in words if w] == shlex.split(ex)


@pytest.mark.parametrize("name", list(lineup.models()))
def test_identity_block(name):
    b = manifest.identity_block(name)
    assert b["pipeline"]["version"] == "1.3.0"
    assert b["model"]["weight_hash"] == lineup.models()[name]["weight_hash"]
    assert b["serializer"]["family"] == lineup.models()[name]["family"]
    assert len(b["lineup"]["sha256"]) == 64 and b["serving"]["batch_size"] == 1
    with pytest.raises(RuntimeError):
        manifest.check_pin(b, "v1.2.0", "0" * 40)



# ---- 1.3.0 serving modes ----
@pytest.mark.parametrize("name", list(lineup.models()))
def test_strict_mode_is_the_1_2_0_command(name):
    """The default mode must serve exactly as 1.2.0: the flag list equals serving.determinism.flags."""
    import lineup
    assert serve.mode_flags("strict") == lineup.lineup()["serving"]["determinism"]["flags"]
    assert serve.vllm_argv(name, python="py") == serve.vllm_argv(name, python="py", mode="strict")
    assert serve.env() == dict(lineup.lineup()["serving"]["determinism"]["env"])


@pytest.mark.parametrize("mode", ["throughput", "throughput_invariant"])
def test_throughput_modes_change_only_named_flags(mode):
    strict, m = serve.mode_flags("strict"), serve.mode_flags(mode)
    assert "--enable-prefix-caching" in m and "--no-enable-prefix-caching" not in m
    assert "--max-num-seqs 32" in m and "--max-num-seqs 1" not in m
    kept = [f for f in strict if f.split()[0] not in ("--max-num-seqs", "--no-enable-prefix-caching")]
    assert all(f in m for f in kept)
    argv = serve.vllm_argv("gemma-2-9b-it", python="py", mode=mode)
    flags = [a for a in argv if a.startswith("--")]
    assert len(flags) == len(set(flags)), flags
    assert (serve.env(mode).get("VLLM_BATCH_INVARIANT") == "1") == (mode == "throughput_invariant")


def test_identity_block_records_mode():
    b = manifest.identity_block("qwen3.5-9b", mode="throughput")
    assert b["serving"]["mode"] == "throughput" and b["serving"]["batch_size"] == 32
    assert manifest.identity_block("qwen3.5-9b")["serving"]["mode"] == "strict"
    with pytest.raises(KeyError):
        serve.mode_flags("nope")
