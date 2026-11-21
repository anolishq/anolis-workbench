"""An unexpected error in a route is a JSON 500, not a dropped connection,
and the two inputs that raised one are handled where they arise (#674)."""

from __future__ import annotations

import http.client
import json
import pathlib
import threading
from typing import Any, Callable, Generator

import pytest

from anolis_workbench.core import launcher
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module
from anolis_workbench.server import app
from anolis_workbench.server.routes import commission


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


def _request(port: int, method: str, path: str) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        body = b"{}" if method == "POST" else None
        conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def _boom(*_args: object, **_kwargs: object) -> None:
    raise AttributeError("'list' object has no attribute 'get'")


@pytest.mark.parametrize(
    ("method", "path", "target"),
    [
        ("GET", "/api/status", "get_status"),
        ("POST", "/api/projects/demo/stop", "stop"),
        ("POST", "/api/projects/demo/preflight", "preflight"),
    ],
)
def test_an_unexpected_error_in_a_route_is_a_json_500(
    port: int, monkeypatch: pytest.MonkeyPatch, method: str, path: str, target: str
) -> None:
    monkeypatch.setattr(commission.launcher_module, target, _boom)
    status, payload = _request(port, method, path)
    assert status == 500
    assert payload == {"error": "'list' object has no attribute 'get'"}


@pytest.mark.parametrize("bad_port", [70000, -1, 0, "8080", True, None])
def test_a_runtime_port_that_is_not_a_tcp_port_is_a_failed_check(bad_port: object) -> None:
    check = launcher._check_port(bad_port)
    assert check["ok"] is False
    assert "not a TCP port" in check["error"]


@pytest.mark.parametrize("content", ["[]", "null", "42", '"text"'])
def test_a_running_json_that_is_not_an_object_is_stale(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    systems = tmp_path / "systems"
    (systems / "rig1").mkdir(parents=True)
    running = systems / "rig1" / "running.json"
    running.write_text(content, encoding="utf-8")
    monkeypatch.setattr(paths_module, "SYSTEMS_ROOT", systems)

    assert launcher._discover_running_runtime(clean_stale=True) is None
    assert not running.exists()
