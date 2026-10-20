"""The manifest wrapper: artifacts, project identity and extras are inside run_id = H(manifest), on both profiles
(ported from the trust study's test_manifest.py, where the artifact was the GLP prior)."""
import json

import pytest

from helpers import reset_profile, use_profile
from llama8b_it_base_probe import manifest as pm

H = {"final.safetensors": "a" * 64, "rep_statistics.pt": "b" * 64, "config.yaml": "c" * 64}
ART = {"prior": {"hf_repo": "generative-latent-prior/glp-llama8b-d6", "revision": "07ce1fae24a08c1864e578e7c302269a3cf4445a",
                 "layer": 15, "file_sha256": H}}


@pytest.fixture
def profile(monkeypatch):
    yield lambda family: use_profile(monkeypatch, family)
    reset_profile(monkeypatch)


def _art(**files):
    a = json.loads(json.dumps(ART))
    a["prior"]["file_sha256"].update(files)
    return a


def test_base_manifest_carries_artifact_and_drops_sae(profile):
    profile("llama3_base")
    rid, m = pm.build(artifacts=ART)
    assert m["artifacts"]["prior"]["revision"] == ART["prior"]["revision"] and m["artifacts"]["prior"]["file_sha256"] == H
    assert m["sae"]["id"] is None and "dropped" in m["sae"]
    assert not any(u.startswith(("sae.", "artifacts.")) for u in m["unverified"])
    assert m["hooks"]["resid_l15"]["hf_hidden_states_index"] == 16 and m["model"]["family"] == "llama3_base"


@pytest.mark.parametrize("field", list(H))
def test_every_artifact_hash_feeds_run_id(profile, field):
    profile("llama3_base")
    rid, _ = pm.build(artifacts=ART)
    rid2, _ = pm.build(artifacts=_art(**{field: "d" * 64}))
    assert rid != rid2 and rid.startswith("run_")


def test_artifact_revision_feeds_run_id(profile):
    profile("llama3_base")
    rid, m = pm.build(artifacts=ART)
    import provenance
    m2 = json.loads(json.dumps(m)); m2["artifacts"]["prior"]["revision"] = "0" * 40
    assert provenance._content_run_id(m2) != rid


def test_missing_artifact_hash_is_unverified(profile):
    profile("llama3_base")
    _, m = pm.build(artifacts=_art(**{"rep_statistics.pt": None}))
    assert "artifacts.prior.file_sha256.rep_statistics.pt" in m["unverified"]


def test_instruct_manifest_keeps_sae_and_has_no_artifacts(profile):
    profile("llama3")
    _, m = pm.build()
    assert m["sae"]["id"] == "layer_19" and m["sae"]["hook"] == "blocks.19.hook_resid_post" and "artifacts" not in m
    assert m["model"]["family"] == "llama3"


def test_extra_identities_feed_run_id(profile):
    profile("llama3")
    a, _ = pm.build(extra={"items_sha256": "1" * 64})
    b, _ = pm.build(extra={"items_sha256": "2" * 64})
    assert a != b


def test_archive_identity_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv(pm.COMMIT_ENV, "f" * 40)
    ident = pm.project_identity(tmp_path)                 # not a git checkout, as on a pod
    assert ident["git_commit"] == "f" * 40 and ident["git_dirty"] is False and ident["source"] == "git archive"
