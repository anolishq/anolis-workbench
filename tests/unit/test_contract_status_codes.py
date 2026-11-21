"""Every status the control handlers return is declared in the contracts (#674).

The OpenAPI validators check each spec against itself. This drives error paths
through the real handlers and checks the response against both specs: the
status must be declared for that operation, and the body must match the
schema declared for it. A handler that grows a new status code then fails here
instead of drifting from the contract.
"""

from __future__ import annotations

import http.client
import json
import pathlib
import re
import threading
from typing import Any, Callable, Generator

import jsonschema
import pytest
import yaml

from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module
from anolis_workbench.server import app
from anolis_workbench.server.routes import commission

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SPECS = {
    name: yaml.safe_load((REPO_ROOT / "contracts" / name).read_text(encoding="utf-8"))
    for name in ("composer-control.openapi.v1.yaml", "workbench-api.openapi.v1.yaml")
}
VERBS = ("get", "post", "put", "delete")


@pytest.fixture()
def port(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, canonical_project: Callable[..., pathlib.Path]
) -> Generator[int, None, None]:
    systems = tmp_path / "systems"
    canonical_project(systems / "demo", machine_id="demo")
    monkeypatch.setattr(paths_module, "SYSTEMS_ROOT", systems)
    monkeypatch.setattr(projects_module, "SYSTEMS_ROOT", systems)
    wb = app._WorkbenchServer(("127.0.0.1", 0), app._Handler)
    wb.config = app.resolve_config("127.0.0.1", 0, False)
    threading.Thread(target=wb.serve_forever, daemon=True).start()
    try:
        yield wb.server_address[1]
    finally:
        wb.shutdown()
        wb.server_close()


def _request(port: int, method: str, path: str, host: str | None = None) -> tuple[int, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        body = b"{}" if method in ("POST", "PUT") else None
        headers = {"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json"}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, json.loads(raw) if raw else None
    finally:
        conn.close()


def _assert_declared(spec_path: str, method: str, status: int, body: Any) -> None:
    """In every spec that has the operation, `status` is declared and `body`
    matches its JSON schema."""
    checked = 0
    for name, spec in SPECS.items():
        operation = spec["paths"].get(spec_path, {}).get(method)
        if operation is None:
            continue
        checked += 1
        responses = operation["responses"]
        assert str(status) in responses, f"{name}: {method.upper()} {spec_path} does not declare {status}"
        response = responses[str(status)]
        if "$ref" in response:
            response = spec["components"]["responses"][response["$ref"].rsplit("/", 1)[-1]]
        schema = response.get("content", {}).get("application/json", {}).get("schema")
        if schema is not None:
            jsonschema.validate(body, {**schema, "components": spec["components"]})
    assert checked, f"{method.upper()} {spec_path} is in neither spec"


def _operations() -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    for spec in SPECS.values():
        for path, item in spec["paths"].items():
            for method in item:
                if method in VERBS:
                    seen.add((method, path))
    return sorted(seen)


@pytest.mark.parametrize(("method", "spec_path"), _operations())
def test_a_foreign_host_is_a_declared_403_on_every_operation(port: int, method: str, spec_path: str) -> None:
    concrete = re.sub(r"\{[^}]+\}", "demo", spec_path)
    status, body = _request(port, method.upper(), concrete, host="evil.example")
    assert status == 403
    _assert_declared(spec_path, method, status, body)


@pytest.mark.parametrize("action", ["preflight", "launch", "stop", "restart", "export"])
def test_an_imported_project_refusal_is_a_declared_409(port: int, monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    monkeypatch.setattr(commission.projects_module, "is_authored", lambda name: False)
    status, body = _request(port, "POST", f"/api/projects/demo/{action}")
    assert status == 409 and body["code"] == "imported_project"
    _assert_declared(f"/api/projects/{{name}}/{action}", "post", status, body)


@pytest.mark.parametrize("action", ["launch", "restart"])
def test_a_project_that_fails_validation_is_a_declared_422(
    port: int, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise commission.launcher_module.LaunchValidationError(["machine-profile is missing machine_id."])

    monkeypatch.setattr(commission.projects_module, "is_authored", lambda name: True)
    monkeypatch.setattr(commission.launcher_module, action, _refuse)
    status, body = _request(port, "POST", f"/api/projects/demo/{action}")
    assert status == 422 and body["errors"] == ["machine-profile is missing machine_id."]
    _assert_declared(f"/api/projects/{{name}}/{action}", "post", status, body)
