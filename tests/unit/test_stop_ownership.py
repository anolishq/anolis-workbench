"""Stopping one project leaves another project's runtime running (#670).

POST /api/projects/<name>/stop checked only that <name> existed, then stopped
whichever runtime was running, whatever project it belonged to, and answered
ok. Restart already refused that mismatch with 409.
"""

from __future__ import annotations

import pathlib

import pytest

from anolis_workbench.core import launcher
from anolis_workbench.server.routes import commission


class _FakeRuntime:
    """Enough of a Popen handle for stop() and the status check."""

    pid = 4242

    def __init__(self) -> None:
        self.alive = True

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.alive = False

    def kill(self) -> None:
        self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _FakeHandler:
    def __init__(self) -> None:
        self.responses: list[tuple[int, dict]] = []

    def _json(self, status: int, data: dict) -> None:
        self.responses.append((status, data))


@pytest.fixture
def terminated(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """No runtime is running; each stub records what stop() would end."""
    monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", tmp_path / "systems")
    monkeypatch.setattr(commission.projects_module, "get_project", lambda name: {})
    monkeypatch.setattr(commission, "_reject_imported", lambda handler, name, action: False)
    monkeypatch.setattr(launcher, "_state", dict(launcher._state, process=None, project=None))
    monkeypatch.setattr(launcher, "_discover_running_runtime", lambda clean_stale=False: None)
    monkeypatch.setattr(launcher, "_discover_external_runtime", lambda: None)
    killed: list[int] = []
    monkeypatch.setattr(launcher, "_terminate_pid", lambda pid, timeout_s=5.0: killed.append(pid))
    monkeypatch.setattr(launcher, "_pid_exists", lambda pid: pid not in killed)
    return killed


def _launched_here(monkeypatch: pytest.MonkeyPatch, project: str) -> _FakeRuntime:
    runtime = _FakeRuntime()
    monkeypatch.setattr(launcher, "_state", dict(launcher._state, process=runtime, project=project))
    return runtime


def _found_in_running_json(monkeypatch: pytest.MonkeyPatch, project: str) -> None:
    detached = {"running": True, "project": project, "pid": 5151, "managed": False}
    monkeypatch.setattr(launcher, "_discover_running_runtime", lambda clean_stale=False: dict(detached))


def _stop(name: str) -> tuple[int, dict]:
    handler = _FakeHandler()
    commission.stop_project(handler, name)
    assert len(handler.responses) == 1
    return handler.responses[0]


def test_stopping_another_project_leaves_a_runtime_launched_here_running(
    terminated: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _launched_here(monkeypatch, "project-b")

    status, body = _stop("project-a")

    assert status == 409
    assert "project-b" in body["error"]
    assert runtime.alive, "project-a's stop ended project-b's runtime"


def test_stopping_another_project_leaves_a_runtime_found_in_running_json_running(
    terminated: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _found_in_running_json(monkeypatch, "project-b")

    status, body = _stop("project-a")

    assert status == 409
    assert "project-b" in body["error"]
    assert terminated == [], "project-a's stop ended project-b's runtime"


def test_a_project_stops_its_own_runtime_launched_here(terminated: list[int], monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _launched_here(monkeypatch, "project-b")

    assert _stop("project-b") == (200, {"ok": True})
    assert not runtime.alive


def test_a_project_stops_its_own_runtime_found_in_running_json(
    terminated: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _found_in_running_json(monkeypatch, "project-b")

    assert _stop("project-b") == (200, {"ok": True})
    assert terminated == [5151]


def test_a_stop_with_nothing_running_is_a_no_op(terminated: list[int]) -> None:
    assert _stop("project-a") == (200, {"ok": True})
    assert terminated == []
