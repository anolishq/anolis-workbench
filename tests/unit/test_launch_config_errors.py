"""An executable that isn't configured is a 422, not busy's 409 (#672).

Launch and restart answered a missing runtime or provider executable with 409,
the status "a runtime is already running" uses, so a client could not tell a
configuration it must fix from a runtime it must stop.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from anolis_workbench.core import launcher
from anolis_workbench.server.routes import commission


class _Handler:
    def __init__(self) -> None:
        self.status: int | None = None
        self.body: dict[str, Any] | None = None

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self.status = status
        self.body = data


def _document(canonical_document, *, runtime: str, provider: str) -> dict[str, Any]:
    document: dict[str, Any] = canonical_document()
    document["host_paths"] = {"runtime_executable": runtime, "providers": {"sim0": {"executable": provider}}}
    return document


@pytest.fixture()
def routes(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> dict[str, Any]:
    """The routes see `state["document"]`; nothing is ever started or stopped."""
    state: dict[str, Any] = {"document": None, "running": False, "stopped": 0}

    def _popen(*args: Any, **kwargs: Any) -> None:
        pytest.fail("the runtime was started")

    def _stop_locked() -> None:
        state["stopped"] += 1

    monkeypatch.setattr(launcher.subprocess, "Popen", _popen)
    monkeypatch.setattr(launcher, "_stop_locked", _stop_locked)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        launcher,
        "_current_runtime_snapshot",
        lambda clean_stale=False: {"running": state["running"], "project": "rig" if state["running"] else None},
    )
    from anolis_workbench.core import projects as projects_module

    monkeypatch.setattr(projects_module, "get_project", lambda name: state["document"])
    monkeypatch.setattr(commission.projects_module, "is_authored", lambda name: True)
    monkeypatch.setattr(commission.projects_module, "project_dir", lambda name: tmp_path)
    return state


@pytest.mark.parametrize("route", [commission.launch_project, commission.restart_project])
@pytest.mark.parametrize(
    ("runtime", "message"),
    [
        ("", "Runtime executable path is missing"),
        ("/nonexistent/anolis-runtime", "does not exist or is not executable"),
    ],
)
def test_a_missing_runtime_executable_is_a_422(
    routes: dict[str, Any], canonical_document, route, runtime: str, message: str
) -> None:
    routes["document"] = _document(canonical_document, runtime=runtime, provider="/opt/anolis/sim")
    routes["running"] = route is commission.restart_project
    handler = _Handler()

    route(handler, "rig")

    assert handler.status == 422, handler.body
    assert handler.body is not None
    assert handler.body["code"] == "runtime_executable_missing"
    assert message in handler.body["error"]
    assert routes["stopped"] == 0


@pytest.mark.parametrize("route", [commission.launch_project, commission.restart_project])
def test_a_missing_provider_executable_is_a_422(routes: dict[str, Any], canonical_document, route) -> None:
    routes["document"] = _document(canonical_document, runtime="/opt/anolis/anolis-runtime", provider="")
    routes["running"] = route is commission.restart_project
    handler = _Handler()

    route(handler, "rig")

    assert handler.status == 422, handler.body
    assert handler.body is not None
    assert handler.body["code"] == "provider_executable_missing"
    assert "sim0" in handler.body["error"]
    assert routes["stopped"] == 0


def test_a_launch_while_a_runtime_runs_is_still_a_409(routes: dict[str, Any], canonical_document) -> None:
    routes["document"] = _document(canonical_document, runtime="", provider="")
    routes["running"] = True
    handler = _Handler()

    commission.launch_project(handler, "rig")

    assert handler.status == 409, handler.body
    assert handler.body is not None
    assert "already running" in handler.body["error"]
