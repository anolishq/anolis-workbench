"""wb#754: the proxy resolves the runtime's target once, not per request.

Resolving read the launcher's status (every project, a runtime probe), the
running project's document, the installed config and the token file on every
proxied request; on a Pi that was ~0.2 s each, and Operate's parallel polls
queued behind each other. The resolved target and token are now kept until a
file they depend on changes, the launcher's own process changes, or the
target refuses a request.
"""

from __future__ import annotations

import os
import pathlib
import urllib.error
import urllib.request
from typing import Any

import pytest

from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module
from anolis_workbench.core import workbench_service
from anolis_workbench.server.routes import operate


class _Handler:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.rfile = None
        self.wfile = _Sink()
        self.status: int | None = None
        self.json_payloads: list[tuple[int, dict[str, Any]]] = []

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, *_args: str) -> None:
        pass

    def end_headers(self) -> None:
        pass

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self.json_payloads.append((status, payload))


class _Sink:
    def write(self, _data: bytes) -> None:
        pass


class _Response:
    status = 200
    headers: dict[str, str] = {}

    def read(self, *_args: Any) -> bytes:
        return b"{}"

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass


@pytest.fixture()
def rig(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A running project "rig" on :8085, installed from a prefix, with every
    lookup counted."""
    systems = tmp_path / "systems"
    project = systems / "rig"
    (project / "config").mkdir(parents=True)
    (project / "machine-profile.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (project / "config" / "runtime.yaml").write_text("http: {port: 8085}\n", encoding="utf-8")
    prefix = tmp_path / "opt"
    (prefix / "config").mkdir(parents=True)
    (prefix / "config" / "runtime.yaml").write_text("http: {port: 8085}\n", encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("t1\n", encoding="utf-8")

    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_URL", raising=False)
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_TOKEN", raising=False)
    monkeypatch.setattr(paths_module, "SYSTEMS_ROOT", systems)
    monkeypatch.setattr(projects_module, "SYSTEMS_ROOT", systems)
    monkeypatch.setattr(workbench_service, "RUNTIME_TOKEN_FILE", token)
    monkeypatch.setattr(operate, "_resolved", None, raising=False)

    counts = {"status": 0, "project": 0, "token": 0}
    state: dict[str, Any] = {"running": True, "port": 8085, "refuse": (), "urls": [], "auth": []}

    def get_status() -> dict:
        counts["status"] += 1
        return {"running": state["running"], "active_project": "rig"}

    def get_project(name: str) -> dict:
        counts["project"] += 1
        return {}

    read_token = workbench_service.read_runtime_token

    def counted_token() -> str:
        counts["token"] += 1
        return read_token()

    def urlopen(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        state["urls"].append(req.full_url)
        state["auth"].append(req.headers.get("Authorization"))
        if req.full_url.startswith(state["refuse"]):
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        return _Response()

    monkeypatch.setattr(operate.launcher_module, "get_status", get_status)
    monkeypatch.setattr(operate.projects_module, "get_project", get_project)
    monkeypatch.setattr(operate.projects_module, "installed_from", lambda name: str(prefix) if name == "rig" else None)
    monkeypatch.setattr(operate.launcher_module, "installed_runtime_target", lambda name: None)
    monkeypatch.setattr(
        operate.launcher_module,
        "active_runtime_config",
        lambda system: {"http": {"bind": "127.0.0.1", "port": state["port"]}},
    )
    monkeypatch.setattr(workbench_service, "read_runtime_token", counted_token)
    monkeypatch.setattr(operate.urllib.request, "urlopen", urlopen)
    return {"counts": counts, "state": state, "project": project, "prefix": prefix, "token": token}


def _get(path: str = "/v0/runtime/status") -> _Handler:
    handler = _Handler()
    operate.proxy_runtime(handler, "GET", path)
    return handler


def _touch(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_polls_resolve_the_target_and_read_the_token_once(rig: dict[str, Any]) -> None:
    for _ in range(7):
        assert _get().status == 200

    assert rig["counts"] == {"status": 1, "project": 1, "token": 1}
    assert rig["state"]["urls"] == ["http://127.0.0.1:8085/v0/runtime/status"] * 7
    assert rig["state"]["auth"] == ["Bearer t1"] * 7


@pytest.mark.parametrize(
    "change",
    ["the project's config", "the project's profile", "the installed config", "a running.json"],
)
def test_a_change_to_a_file_it_was_read_from_resolves_again(rig: dict[str, Any], change: str) -> None:
    _get()
    rig["state"]["port"] = 8086
    {
        "the project's config": lambda: _touch(rig["project"] / "config" / "runtime.yaml", "http: {port: 8086}\n"),
        "the project's profile": lambda: _touch(rig["project"] / "machine-profile.yaml", "schema_version: 1\n#\n"),
        "the installed config": lambda: _touch(rig["prefix"] / "config" / "runtime.yaml", "http: {port: 8086}\n"),
        "a running.json": lambda: _touch(rig["project"] / "running.json", '{"pid": 1}'),
    }[change]()

    _get()

    assert rig["counts"]["project"] == 2
    assert rig["state"]["urls"][-1] == "http://127.0.0.1:8086/v0/runtime/status"


def test_a_new_token_is_read_when_its_file_changes(rig: dict[str, Any]) -> None:
    _get()
    _touch(rig["token"], "t2\n")

    _get()

    assert rig["state"]["auth"] == ["Bearer t1", "Bearer t2"]


def test_a_refused_target_is_resolved_afresh(rig: dict[str, Any]) -> None:
    _get()
    rig["state"]["port"] = 8086  # another runtime, nothing on disk changed
    rig["state"]["refuse"] = ("http://127.0.0.1:8085",)

    handler = _get()

    assert handler.status == 200
    assert rig["state"]["urls"][-2:] == [
        "http://127.0.0.1:8085/v0/runtime/status",
        "http://127.0.0.1:8086/v0/runtime/status",
    ]


def test_a_stopped_runtime_still_reads_not_running(rig: dict[str, Any]) -> None:
    _get()
    rig["state"]["running"] = False
    rig["state"]["refuse"] = ("http://127.0.0.1:8085",)

    handler = _get()

    assert handler.json_payloads == [(503, {"error": "Runtime is not running"})]
    assert rig["counts"]["status"] == 2


def test_the_override_is_not_served_from_a_cached_local_target(
    rig: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _get()
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_URL", "http://192.0.2.10:8080")

    _get()

    assert rig["state"]["urls"][-1] == "http://192.0.2.10:8080/v0/runtime/status"
    assert rig["state"]["auth"][-1] is None
