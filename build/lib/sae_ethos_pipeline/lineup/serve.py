"""The vLLM command line for a lineup model on the lineup serving stack (lineup-serve-cmd NAME).

Built from the model's profile (model, revision, served name, dtype, context) plus the lineup's determinism flags
(lineup.yaml serving.determinism); a flag the profile already sets is not repeated. Multimodal checkpoints
(hf_auto_class AutoModelForImageTextToText: Gemma-3-27B, Qwen3.5) are served text-only."""
from __future__ import annotations

import os
import shlex
import sys

import yaml

import lineup

VENV_PY = "/workspace/venv_vllm_lineup/bin/python"
TEXT_ONLY = "--limit-mm-per-prompt.image 0 --limit-mm-per-prompt.video 0"   # vLLM's dotted form: no JSON to quote


def _flag(s):
    return s.split()[0]


def vllm_argv(name: str, python: str = VENV_PY, port: int = 8000) -> list[str]:
    e = lineup.models()[name]
    prof = yaml.safe_load((lineup.CONFIG / e["profile"]).read_text())
    tm, s = prof["target_model"], prof.get("serve") or {}
    det = lineup.lineup()["serving"]["determinism"]["flags"]
    argv = [python, "-m", "vllm.entrypoints.openai.api_server", "--model", tm["hf_id"],
            "--revision", tm["revision"], "--tokenizer-revision", tm["revision"],
            "--served-model-name", prof["endpoint"]["served_model_name"],
            "--max-model-len", str(s.get("max_model_len", 8192)),
            "--tensor-parallel-size", str(s.get("tensor_parallel_size", 1)),
            "--gpu-memory-utilization", "0.9", "--port", str(port)]
    extra = shlex.split(s.get("extra_args") or "")
    have = set(a for a in argv + extra if a.startswith("--"))
    for f in det:
        if _flag(f) not in have:
            extra += shlex.split(f)
            have.add(_flag(f))
    if (prof.get("replay") or {}).get("hf_auto_class") == "AutoModelForImageTextToText" and not any(h.startswith("--limit-mm-per-prompt") for h in have):
        extra += shlex.split(TEXT_ONLY)
    if "--dtype" not in have:
        extra += ["--dtype", s.get("dtype", "bfloat16")]
    return argv + extra


def env() -> dict:
    return dict(lineup.lineup()["serving"]["determinism"]["env"])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in lineup.models():
        print(f"usage: lineup-serve-cmd NAME   (NAME in {list(lineup.models())})", file=sys.stderr)
        return 2
    pre = " ".join(f"{k}={shlex.quote(v)}" for k, v in env().items())
    print(pre + " " + shlex.join(vllm_argv(argv[0], python=os.environ.get("LINEUP_VLLM_PYTHON", VENV_PY))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
