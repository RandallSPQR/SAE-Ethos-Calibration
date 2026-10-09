"""Adding a model family from outside (python -m model_io.test_registry): an adapter registered at runtime, a profile
file outside config/ (MODEL_PROFILE_FILE), the adapter checks, and the built-in families unchanged."""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import model_io                                                  # noqa: E402


def fake_llama():
    m = types.ModuleType("fake_llama3")
    m.FAMILY = "llama3"; m.END_OF_TURN = "<|eot_id|>"; m.TURN_SUFFIX = "<|eot_id|>"
    m.GENERATION_PROMPT = "<|start_header_id|>assistant<|end_header_id|>\n\n"
    m.user_turn = lambda t: f"<|start_header_id|>user<|end_header_id|>\n\n{t}<|eot_id|>"
    m.serialize_messages = lambda msgs, add_generation_prompt=True: "".join(m.user_turn(x["content"]) for x in msgs) + (
        m.GENERATION_PROMPT if add_generation_prompt else "")
    m.apply_to_tokenizer = lambda tok, msgs, add_generation_prompt=True: [0]
    m.prompt_hash = lambda msgs: "x"
    return m


def run():
    res = {}
    res["built-ins: gemma2 and gemma3 resolve and satisfy the adapter interface"] = all(
        model_io.check_adapter(model_io.get(f)) for f in ("gemma2", "gemma3"))
    try:
        model_io.get("llama3"); unknown = False
    except KeyError as e:
        unknown = "register" in str(e)
    res["an unknown family raises KeyError naming the two ways to add one"] = unknown
    bad = types.ModuleType("bad"); bad.FAMILY = "bad"
    try:
        model_io.register("bad", bad); rejected = False
    except TypeError:
        rejected = True
    res["an adapter missing interface members is rejected"] = rejected
    model_io.register("llama3", fake_llama())
    res["register(): the new family resolves"] = model_io.get("llama3").FAMILY == "llama3" and "llama3" in model_io.known()
    prof = Path(tempfile.mkdtemp()) / "models_llama-3.1-8b-it.yaml"
    prof.write_text((ROOT / "config" / "models_gemma-3-27b-it.yaml").read_text().replace("family: gemma3", "family: llama3"))
    os.environ["MODEL_PROFILE_FILE"] = str(prof)
    import importlib, modelcfg
    importlib.reload(modelcfg)
    ok = modelcfg.models_path() == prof.resolve() and modelcfg.profile() == "llama-3.1-8b-it" and modelcfg.family() == "llama3"
    res["MODEL_PROFILE_FILE: a profile outside config/ selects the registered adapter through modelcfg.serializer()"] = (
        ok and modelcfg.serializer().GENERATION_PROMPT.startswith("<|start_header_id|>assistant"))
    del os.environ["MODEL_PROFILE_FILE"]
    importlib.reload(modelcfg)
    res["unset: modelcfg reads config/ exactly as before"] = modelcfg.models_path() == ROOT / "config" / (
        f"models_{os.environ['MODEL_PROFILE']}.yaml" if os.environ.get("MODEL_PROFILE") else "models.yaml")
    return res


if __name__ == "__main__":
    r = run()
    for k, v in r.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in r.items() if not v]
    print(f"\n{len(r) - len(bad)}/{len(r)} passed")
    sys.exit(1 if bad else 0)
