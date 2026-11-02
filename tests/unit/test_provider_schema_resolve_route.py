"""POST /api/provider-schemas/resolve: the envelope each pin resolves to (#784).

The composer renders forms and client-side checks from this, so they show
the envelope a save validates against, imports included, and follow a pin
as it is edited.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib

import pytest

from anolis_workbench.core import provider_import, provider_schemas
from anolis_workbench.server.routes import compose


@pytest.fixture(autouse=True)
def _imports_dir(monkeypatch, tmp_path):
    directory = tmp_path / "imports"
    monkeypatch.setattr(provider_schemas.paths_module, "IMPORTED_PROVIDER_SCHEMAS_DIR", directory)
    provider_schemas._envelope_cache = None
    provider_schemas._reset_imports_cache()
    yield directory
    provider_schemas._envelope_cache = None
    provider_schemas._reset_imports_cache()


class _Handler:
    def __init__(self, body: object) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.sent: list[tuple[int, dict]] = []

    def _json(self, code: int, payload: dict) -> None:
        self.sent.append((code, payload))

    def _body_json(self, max_bytes: int = 1_048_576):
        from anolis_workbench.server import app

        return app._Handler._body_json(self, max_bytes)  # type: ignore[arg-type]


def _post(body: object) -> tuple[int, dict]:
    handler = _Handler(body)
    compose.resolve_provider_schemas(handler)
    return handler.sent[0]


def _packaged_version(kind: str) -> str:
    version = provider_schemas._load_envelopes()[kind]["provider_version"]
    assert isinstance(version, str)
    return version


def _import(tmp_path: pathlib.Path, kind: str, version: str, extra: str) -> str:
    doc = json.loads(json.dumps(provider_schemas._load_envelopes()["sim"]))
    doc["provider"] = f"anolis-provider-{kind}"
    doc["provider_version"] = version
    doc["schema"].setdefault("properties", {})[extra] = {"type": "string"}
    path = tmp_path / f"{kind}-{version}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    provider_import.import_file(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_kind_resolves_at_its_pin(tmp_path):
    sha = _import(tmp_path, "sim", "0.1.0", "only_in_0_1_0")
    code, body = _post({"pins": {"sim": "0.1.0", "bread": _packaged_version("bread")}})
    assert code == 200
    assert body["schema_version"] == 1
    assert set(body["providers"]) == set(provider_schemas.available_kinds())

    sim = body["resolution"]["sim"]
    assert sim == {
        "source": "sideloaded",
        "exact": True,
        "pinned_version": "0.1.0",
        "envelope_version": "0.1.0",
        "sha256": sha,
    }
    assert "only_in_0_1_0" in body["providers"]["sim"]["schema"]["properties"]

    bread = body["resolution"]["bread"]
    assert bread["source"] == "packaged" and bread["exact"] and bread["sha256"] is None


def test_an_unpinned_or_other_pin_gets_the_packaged_envelope(tmp_path):
    _import(tmp_path, "sim", "0.1.0", "only_in_0_1_0")
    for pins in ({}, {"sim": _packaged_version("sim")}):
        code, body = _post({"pins": pins})
        assert code == 200
        assert body["resolution"]["sim"]["source"] == "packaged"
        assert "only_in_0_1_0" not in body["providers"]["sim"]["schema"].get("properties", {})


def test_a_pin_the_workbench_cant_satisfy_says_so():
    code, body = _post({"pins": {"bread": "0.0.1"}})
    assert code == 200
    bread = body["resolution"]["bread"]
    assert bread["exact"] is False
    assert bread["pinned_version"] == "0.0.1"
    assert bread["envelope_version"] == _packaged_version("bread")


def test_a_pin_is_stripped_as_the_save_path_strips_it():
    code, body = _post({"pins": {"bread": f" {_packaged_version('bread')} "}})
    assert code == 200
    assert body["resolution"]["bread"]["exact"] is True


@pytest.mark.parametrize("body", [{"pins": ["bread"]}, {"pins": {"bread": 1}}, {"nope": {}}, ["x"]])
def test_a_malformed_body_is_refused(body):
    code, payload = _post(body)
    assert code == 400
    assert "pins" in payload["error"]
