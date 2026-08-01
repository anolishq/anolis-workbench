"""Provider config-schema envelope registry (#270)."""

import json

import pytest

from anolis_workbench.core import provider_schemas


@pytest.fixture(autouse=True)
def _fresh_cache():
    provider_schemas._envelope_cache = None
    yield
    provider_schemas._envelope_cache = None


def test_available_kinds_are_the_vendored_envelopes():
    assert provider_schemas.available_kinds() == ["bread", "ezo", "sim"]


def test_envelopes_carry_the_profile_shape():
    for kind in provider_schemas.available_kinds():
        envelope = provider_schemas.get_envelope(kind)
        assert envelope is not None
        assert isinstance(envelope["config_schema_version"], int)
        assert envelope["config_schema_version"] >= 1
        assert isinstance(envelope["schema"], dict)
        assert envelope["provider"] == f"anolis-provider-{kind}"


def test_unknown_kind_is_none():
    assert provider_schemas.get_envelope("custom") is None
    assert provider_schemas.get_envelope("nope") is None


def test_all_envelopes_matches_kinds():
    envelopes = provider_schemas.all_envelopes()
    assert sorted(envelopes.keys()) == provider_schemas.available_kinds()


def test_malformed_envelope_fails_loudly(monkeypatch, tmp_path):
    (tmp_path / "bogus.config-schema.json").write_text(json.dumps({"config_schema_version": True, "schema": {}}))
    monkeypatch.setattr(provider_schemas.paths_module, "PROVIDER_SCHEMAS_DIR", tmp_path)
    provider_schemas._envelope_cache = None
    with pytest.raises(ValueError, match="config_schema_version"):
        provider_schemas.available_kinds()
