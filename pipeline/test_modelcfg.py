"""Tests for the model-profile loader (python -m test_modelcfg). No GPU, no model download: fake tokenizers and fake
module trees stand in for the box. What must hold: the 9B default resolves to exactly the values the closed study ran
with; the 27B profile resolves to its verified values; a tokenizer that contradicts the profile is refused; the decoder
list is found at Gemma-3's nested path and refused at a wrong count; the shell env carries the same values."""
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent


def fresh(profile):
    if profile:
        os.environ["MODEL_PROFILE"] = profile
    else:
        os.environ.pop("MODEL_PROFILE", None)
    import modelcfg
    importlib.reload(modelcfg)
    modelcfg._load.cache_clear()
    return modelcfg


class FakeTok:
    def __init__(self, eot, eos, suffix_ids):
        self.eot, self.eos_token_id, self.suffix_ids = eot, eos, suffix_ids

    def convert_tokens_to_ids(self, t):
        return self.eot if t == "<end_of_turn>" else 3

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": list(self.suffix_ids) if text == "<end_of_turn>\n" else [9]}


def layers(n):
    return [object() for _ in range(n)]


def main():
    res = {}
    m = fresh(None)
    res["9b_default_file"] = m.models_path().name == "models.yaml"
    res["9b_family_stops"] = m.family() == "gemma2" and m.stop_token_ids() == [107, 1]
    res["9b_hooks_layer31"] = list(m.hook_names()) == ["blocks.31.hook_resid_post", "layers.31.hidden_states_only",
                                                        "layers.31.mlp_output", "layers.31.attn_output", "layers.31.input_resid"]
    res["9b_replay_cfg"] = m.replay_cfg() == {"dtype": "float32", "device_map": "cuda", "attn_implementation": "eager",
                                              "layers_path": "model.layers",
                                              "hf_auto_class": "AutoModelForCausalLM", "nnsight_class": "LanguageModel"}
    res["9b_serializer_is_gemma2"] = m.serializer().__name__ == "model_io.gemma2"
    res["9b_neuronpedia"] = m.neuronpedia() == {"model_id": "gemma-2-9b-it", "source": "31-gemmascope-res-16k"}
    res["9b_tokenizer_ok"] = m.check_tokenizer(FakeTok(107, 1, [107, 108]))["end_of_turn_id"] == 107

    m = fresh("gemma-3-27b-it")
    res["27b_file"] = m.models_path().name == "models_gemma-3-27b-it.yaml"
    res["27b_family_stops"] = m.family() == "gemma3" and m.stop_token_ids() == [106, 1]
    res["27b_hooks_layer40"] = "blocks.40.hook_resid_post" in m.hook_names() and m.sae_layer() == 40
    res["27b_serve_bf16_replay_fp32"] = m.serve_cfg()["dtype"] == "bfloat16" and m.replay_cfg()["dtype"] == "float32" \
        and m.replay_cfg()["device_map"] == "auto"
    res["27b_serializer_is_gemma3"] = m.serializer().__name__ == "model_io.gemma3"
    res["27b_tokenizer_ok"] = m.check_tokenizer(FakeTok(106, 1, [106, 107]))["stop_token_ids"] == [1, 106]
    try:
        m.check_tokenizer(FakeTok(107, 1, [107, 108]))          # a Gemma-2 tokenizer under the 27B profile
        res["27b_refuses_gemma2_tokenizer"] = False
    except RuntimeError:
        res["27b_refuses_gemma2_tokenizer"] = True
    try:
        m.check_tokenizer(FakeTok(106, 1, [9, 106]))            # suffix that does not start with <end_of_turn>
        res["27b_refuses_bad_suffix"] = False
    except RuntimeError:
        res["27b_refuses_bad_suffix"] = True
    g3 = SimpleNamespace(config=SimpleNamespace(text_config=SimpleNamespace(num_hidden_layers=62)),
                         model=SimpleNamespace(language_model=SimpleNamespace(layers=layers(62))))
    res["27b_nested_layers_found"] = len(m.decoder_layers(g3)) == 62
    wrong = SimpleNamespace(config=SimpleNamespace(num_hidden_layers=62), model=SimpleNamespace(layers=layers(27)))
    try:
        m.decoder_layers(wrong)                                 # e.g. the 27-layer vision tower's list
        res["wrong_count_refused"] = False
    except RuntimeError:
        res["wrong_count_refused"] = True
    g2 = SimpleNamespace(config=SimpleNamespace(num_hidden_layers=42), model=SimpleNamespace(layers=layers(42)))
    res["flat_layers_found"] = len(m.decoder_layers(g2, "model.layers")) == 42

    # SAE_ROLE=secondary: every consumer sees the layer-53 block as `sae`; the primary view names it as the secondary
    res["27b_secondary_named"] = (m.secondary_sae() or {}).get("layer") == 53
    os.environ["SAE_ROLE"] = "secondary"
    importlib.reload(m)
    res["27b_role_secondary_layer53"] = m.sae_layer() == 53 and "blocks.53.hook_resid_post" in m.hook_names() \
        and m.neuronpedia()["source"] == "53-gemmascope-2-res-16k" and m.secondary_sae() is None
    os.environ.pop("SAE_ROLE")
    importlib.reload(m)
    res["27b_role_back_to_primary"] = m.sae_layer() == 40
    import calibrate.preflight_weights as pw
    res["27b_preflight_fetches_both_saes"] = [r[2][0] for r in pw.default_repos(m.models())[1:]] == [
        "resid_post/layer_40_width_16k_l0_medium/*", "resid_post/layer_53_width_16k_l0_medium/*"]

    # shell env from calibrate/model_env.sh, both profiles (python on PATH = this interpreter)
    shim = HERE / ".test_bin"
    shim.mkdir(exist_ok=True)
    (shim / "python").unlink(missing_ok=True)
    (shim / "python").symlink_to(sys.executable)
    for prof, want in ((None, ("google/gemma-2-9b-it", "float32", "float32", "cuda")),
                       ("gemma-3-27b-it", ("google/gemma-3-27b-it", "bfloat16", "float32", "auto"))):
        env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"}
        env.pop("MODEL_PROFILE", None)
        if prof:
            env["MODEL_PROFILE"] = prof
        out = subprocess.run(["bash", "-c", "source calibrate/model_env.sh >/dev/null && echo "
                              "\"$TARGET_HF_ID|$TARGET_SERVED_DTYPE|$REPLAY_DTYPE|$REPLAY_DEVICE_MAP\""],
                             cwd=HERE, env=env, capture_output=True, text=True).stdout.strip()
        res[f"model_env_{prof or '9b'}"] = out == "|".join(want)
    (shim / "python").unlink()
    shim.rmdir()
    fresh(None)
    for k, v in res.items():
        print(("PASS " if v else "FAIL ") + k)
    bad = [k for k, v in res.items() if not v]
    print(f"\n{len(res) - len(bad)}/{len(res)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
