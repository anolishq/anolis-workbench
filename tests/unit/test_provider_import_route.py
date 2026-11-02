"""POST /api/provider-schemas/import: a provider schema imported over HTTP (#787).

The desktop app has no CLI on PATH, so it imports through this route. It runs
the CLI's checks behind the request-origin gate, and a running workbench
offers the imported kind at once.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import pathlib
import threading
import urllib.parse
from collections.abc import Generator
from typing import Any

import pytest

from anolis_workbench.core import provider_import, provider_schemas


@pytest.fixture
def server(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, Any], None, None]:
    from anolis_workbench.core import projects as projects_module
    from anolis_workbench.server import app

    imports = tmp_path / "schemas" / "providers"
    monkeypatch.setattr(provider_schemas.paths_module, "IMPORTED_PROVIDER_SCHEMAS_DIR", imports)
    systems = tmp_path / "systems"
    systems.mkdir()
    monkeypatch.setattr(projects_module, "SYSTEMS_ROOT", systems)
    provider_schemas._envelope_cache = None
    provider_schemas._reset_imports_cache()

    wb = app._WorkbenchServer(("127.0.0.1", 0), app._Handler)
    wb.config = app.resolve_config("127.0.0.1", 0, False)
    threading.Thread(target=wb.serve_forever, daemon=True).start()
    try:
        yield {"port": wb.server_address[1], "imports": imports}
    finally:
        wb.shutdown()
        wb.server_close()
        provider_schemas._envelope_cache = None
        provider_schemas._reset_imports_cache()


def _envelope(kind: str = "foo", version: str = "0.1.0", **change: Any) -> bytes:
    doc = json.loads(json.dumps(provider_schemas._load_envelopes()["sim"]))
    doc["provider"] = f"anolis-provider-{kind}"
    doc["provider_version"] = version
    doc["schema"].setdefault("properties", {})["only_in_import"] = {"type": "string"}
    doc.update(change)
    return json.dumps(doc).encode()


def _request(
    port: int, method: str, path: str, body: bytes | None = None, origin: str | None = "same"
) -> tuple[int, dict[str, Any]]:
    host = f"127.0.0.1:{port}"
    headers = {"Host": host, "Content-Type": "application/json"}
    if origin == "same":
        headers["Origin"] = f"http://{host}"
    elif origin is not None:
        headers["Origin"] = origin
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def _import(port: int, raw: bytes, origin: str | None = "same", **query: str) -> tuple[int, dict[str, Any]]:
    path = "/api/provider-schemas/import"
    if query:
        path += "?" + urllib.parse.urlencode(query)
    return _request(port, "POST", path, raw, origin)


def test_an_import_is_offered_at_once_without_a_restart(server):
    raw = _envelope()
    code, body = _import(server["port"], raw, filename="foo-0.1.0.json")
    assert code == 200
    assert body == {
        "kind": "foo",
        "version": "0.1.0",
        "status": "imported",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "repo": "anolishq/anolis-provider-foo",
    }

    code, schemas = _request(server["port"], "GET", "/api/provider-schemas")
    assert code == 200 and "foo" in schemas["providers"]
    code, resolved = _request(server["port"], "POST", "/api/provider-schemas/resolve", b'{"pins": {"foo": "0.1.0"}}')
    assert code == 200
    assert resolved["resolution"]["foo"]["source"] == "sideloaded"
    assert "only_in_import" in resolved["providers"]["foo"]["schema"]["properties"]


def test_the_upload_is_recorded_as_its_origin(server):
    _import(server["port"], _envelope(), filename="foo-0.1.0.json")
    (entry,) = provider_import.list_imports()
    assert entry.origin == "upload: foo-0.1.0.json"


def test_a_cross_origin_import_is_refused_and_writes_nothing(server):
    code, _ = _import(server["port"], _envelope(), origin="https://example.com")
    assert code == 403
    assert not server["imports"].exists() or list(server["imports"].iterdir()) == []


def test_a_refused_file_gets_the_clis_reason(server, tmp_path):
    raw = _envelope(schema={"$ref": "https://example.com/x.json"})
    source = tmp_path / "bad.json"
    source.write_bytes(raw)
    with pytest.raises(provider_import.ImportRefused) as refused:
        provider_import.import_file(source)

    code, body = _import(server["port"], raw)
    assert code == 400
    assert body == {"error": str(refused.value)}
    assert not server["imports"].exists() or list(server["imports"].iterdir()) == []


def test_other_content_needs_replace_and_repo_is_taken(server):
    assert _import(server["port"], _envelope())[0] == 200
    changed = _envelope(description="changed")
    code, body = _import(server["port"], changed)
    assert code == 400 and "--replace" in body["error"]

    code, body = _import(server["port"], changed, replace="1", repo="example/anolis-provider-foo")
    assert code == 200
    assert body["status"] == "replaced" and body["repo"] == "example/anolis-provider-foo"

    code, body = _import(server["port"], changed, repo="../evil")
    assert code == 400 and "repo" in body["error"]


def test_a_body_over_the_envelope_limit_is_refused_unread(server):
    raw = b" " * (provider_import.MAX_ENVELOPE_BYTES + 1)
    code, body = _import(server["port"], raw)
    assert code == 400 and "too large" in body["error"]


def test_an_empty_body_is_refused(server):
    code, body = _import(server["port"], b"")
    assert code == 400 and body["error"]
