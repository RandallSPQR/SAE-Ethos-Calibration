"""capture edge cases on a tiny random Llama (the full path is exercised by example/dry_weather): a spec with no spans
captures the last position only; an also_last readout ordered BEFORE the primary is captured; a prefilled readout reads
after its prefill; activations equal a direct forward pass at hidden_states[L + 1]; spec errors are raised early."""
import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")
from helpers import setup, tiny_llama  # noqa: E402
from llama8b_it_base_probe import capture  # noqa: E402
from llama8b_it_base_probe.spec import ProjectSpec, Readout  # noqa: E402

ITEMS = [{"id": f"i{k}", "text": t} for k, t in enumerate(["The road is wet.", "The sky is clear.", "It is snowing."])]
Q = lambda it: [{"role": "user", "content": f"{it['text']}\nRate how dry it is, 1-3. Answer with the number only."}]
DIGITS = tuple((str(i),) for i in range(1, 4))


def test_last_only_and_also_last_before_primary(tmp_path):
    tok, ser = setup("llama3")
    spec = ProjectSpec(readouts={"llama3": (Readout("first", Q, levels=DIGITS), Readout("second", Q, levels=DIGITS,
                                                                                         prefill="My answer: "))},
                       primary={"llama3": "second"}, layers=(3, 7), also_last=("first",))
    model = tiny_llama(n_layers=8)
    capture.run_capture(ITEMS, spec, tmp_path, tok, model, ser, log_every=0)
    z = np.load(tmp_path / "acts_L7.npz")
    assert set(z.files) == {"last", "last_first"} and z["last"].shape == (3, 64)
    ids = tok(ser.serialize_messages(Q(ITEMS[1]), add_generation_prompt=True) + "My answer: ",
              add_special_tokens=True)["input_ids"]
    with torch.no_grad():
        hs = model(torch.tensor([ids]), output_hidden_states=True).hidden_states
    assert np.allclose(z["last"][1], hs[8][0, -1].numpy().astype(np.float16), atol=1e-3)
    assert not np.allclose(z["last"][1], z["last_first"][1])          # different prompts (prefill)
    assert json.loads((tmp_path / "positions.json").read_text())[0] == {"id": "i0", "last": len(
        tok(ser.serialize_messages(Q(ITEMS[0]), add_generation_prompt=True) + "My answer: ", add_special_tokens=True)["input_ids"]) - 1}


def test_spec_errors_are_early():
    tok, ser = setup("llama3")
    spec = ProjectSpec(readouts={"llama3": (Readout("a", Q, levels=DIGITS),)}, primary={"llama3": "missing"})
    with pytest.raises(KeyError):
        capture.run_capture(ITEMS, spec, "/nonexistent", tok, None, ser)
    spec = ProjectSpec(readouts={"llama3_base": (Readout("a", Q, options=("1", "2")),)}, primary={"llama3_base": "a"})
    with pytest.raises(KeyError):
        capture.run_capture(ITEMS, spec, "/nonexistent", tok, None, ser)
