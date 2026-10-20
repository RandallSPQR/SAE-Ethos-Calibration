"""The example project end to end on CPU (as the trust study's p2/test_v2_cpu.py capture cases): every readout passes
its token check on both real tokenizers over all items; capture on a tiny random Llama writes a distribution per
readout, activations aligned to the item ids at the declared positions, and positions that decode to the item's own
words; --readouts-only writes readouts and no activations; the CLI runs through the profile and the spec reference."""
import json
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
from helpers import FAMILY_TOK, reset_profile, setup, tiny_llama, use_profile  # noqa: E402
from dry_weather.spec import SPEC  # noqa: E402
from llama8b_it_base_probe import capture, load, tokens  # noqa: E402

ITEMS = Path(__file__).with_name("items.jsonl")
FAMILIES = ["llama3_base", "llama3"]


def _items():
    return [json.loads(l) for l in open(ITEMS)]


@pytest.mark.parametrize("family", FAMILIES)
def test_every_readout_passes_its_token_check(family):
    tok, ser = setup(family)
    for ro in SPEC.readouts_for(family):
        for it in _items():
            r = tokens.check_readout(tok, ser, ro, it)
            assert r["pass"], (family, ro.name, it["id"], r)


@pytest.mark.parametrize("family", FAMILIES)
def test_capture_writes_aligned_readouts_and_activations(tmp_path, family):
    tok, ser = setup(family)
    items = _items()[:5]
    meta = capture.run_capture(items, SPEC, tmp_path, tok, tiny_llama(), ser, model_id="tiny", log_every=0)
    recs = [json.loads(l) for l in open(tmp_path / "readouts.jsonl")]
    assert [r["id"] for r in recs] == [it["id"] for it in items] and all(r["set"] == "dev" for r in recs)
    for r in recs:
        assert set(r["readouts"]) == {"expect", "verbal"}
        for v in r["readouts"].values():
            assert abs(sum(v["p"]) - 1) < 1e-6 and len(v["p"]) == 6 and 0 <= v["mass"] <= 1
            assert v["mass"] == 0 or 1 <= v["expected"] <= 5
    assert meta["primary_readout"] == SPEC.primary[family] and meta["family"] == family
    assert json.loads((tmp_path / "item_ids.json").read_text()) == [it["id"] for it in items]
    for L in SPEC.layers:
        z = np.load(tmp_path / f"acts_L{L}.npz")
        assert set(z.files) == {"last", "evidence_last", "evidence_mean", "cue_words", "last_expect", "last_verbal"}
        assert z["last"].shape == z["evidence_mean"].shape == (5, 64) and z["cue_words"].shape == (5, 2, 64)
        assert np.array_equal(z[f"last_{SPEC.primary[family]}"], z["last"])
        assert not np.array_equal(z["last_expect"], z["last_verbal"])
    pos = json.loads((tmp_path / "positions.json").read_text())
    for it, p in zip(items, pos):
        ro = next(r for r in SPEC.readouts_for(family) if r.name == SPEC.primary[family])
        ids = tok(ser.serialize_messages(ro.messages(it), add_generation_prompt=True), add_special_tokens=True)["input_ids"]
        assert p["id"] == it["id"] and p["last"] == len(ids) - 1
        assert tok.decode([ids[i] for i in p["evidence"]]).strip() == it["evidence"]
        for c, i in zip(it["cue_words"], p["cue_words"]):
            assert tok.decode([ids[i]]).strip() and tok.decode([ids[i]]).strip() in c


@pytest.mark.parametrize("family", FAMILIES)
def test_readouts_only_writes_no_activations(tmp_path, family):
    tok, ser = setup(family)
    meta = capture.run_capture(_items()[:3], SPEC, tmp_path, tok, tiny_llama(), ser, readouts_only=True, log_every=0)
    assert meta["readouts_only"] is True and not list(tmp_path.glob("acts_L*.npz"))
    assert json.loads((tmp_path / "positions.json").read_text()) == []
    assert len((tmp_path / "readouts.jsonl").read_text().splitlines()) == 3


@pytest.mark.parametrize("family", FAMILIES)
def test_cli_through_profile_and_spec_reference(tmp_path, monkeypatch, family):
    tok, _ = setup(family)
    use_profile(monkeypatch, family)
    monkeypatch.setattr(load, "load_model", lambda dtype: (tok, tiny_llama()))
    capture.main(["--spec", "dry_weather.spec:SPEC", "--items", str(ITEMS), "--out", str(tmp_path / "o"), "--limit", "2"])
    meta = json.loads((tmp_path / "o" / "capture_meta.json").read_text())
    assert meta["family"] == family and meta["n_items"] == 2 and meta["model"].startswith("meta-llama/Llama-3.1-8B")
    assert FAMILY_TOK[family].startswith("Llama-3.1-8B")
    reset_profile(monkeypatch)
