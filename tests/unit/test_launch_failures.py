"""A launch or restart that fails leaves what was there (anolishq/anolis-workbench#672).

latest.log was opened for writing, which empties it, before the runtime was
started, so a launch whose spawn failed lost the previous run's log. And
restart() checked the runtime executable only after it had stopped the
running runtime, so a refused restart left nothing running.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from anolis_workbench.core import launcher


class _FakeRuntime:
    """Enough of a Popen handle for launch(), stop() and the status check."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.stdout = None
        self.alive = True

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.alive = False

    def kill(self) -> None:
        self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def host(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The project's host paths, which a test may edit; spawns record a _FakeRuntime."""
    exe = tmp_path / "anolis-runtime"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    config = tmp_path / "launch.yaml"
    config.write_text("{}\n", encoding="utf-8")
    paths = {"runtime_executable": str(exe)}
    monkeypatch.setattr("anolis_workbench.core.paths.DATA_ROOT", tmp_path)
    monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", tmp_path / "systems")
    monkeypatch.setattr(launcher, "materialize_launch_config", lambda project_dir, system: config)
    monkeypatch.setattr("anolis_workbench.core.canonical_validator.validate_project", lambda system: [])
    monkeypatch.setattr("anolis_workbench.core.projects.get_project", lambda name: {})
    monkeypatch.setattr(launcher.canonical, "host_paths", lambda doc: dict(paths))
    monkeypatch.setattr(launcher, "_discover_running_runtime", lambda clean_stale=False: None)
    monkeypatch.setattr(launcher, "_discover_external_runtime", lambda: None)
    monkeypatch.setattr(launcher, "_state", dict(launcher._state, process=None, project=None, log_lines_by_project={}))
    monkeypatch.setattr(launcher, "_read_logs", lambda proc, log_file, project: None)
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: _FakeRuntime(pid=4242))
    return paths


def _previous_log(project_dir: pathlib.Path) -> pathlib.Path:
    log = project_dir / "logs" / "latest.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("previous run log\n", encoding="utf-8")
    return log


def test_a_launch_whose_spawn_fails_keeps_the_previous_runs_log(
    host: dict[str, str], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "demo"
    log = _previous_log(project_dir)

    def _popen(*args: Any, **kwargs: Any) -> _FakeRuntime:
        raise FileNotFoundError(2, "No such file or directory", "/nowhere")

    monkeypatch.setattr(launcher.subprocess, "Popen", _popen)

    with pytest.raises(FileNotFoundError):
        launcher.launch("demo", {}, project_dir)

    assert log.read_text(encoding="utf-8") == "previous run log\n"


def test_a_launch_with_no_runtime_executable_keeps_the_previous_runs_log(
    host: dict[str, str], tmp_path: pathlib.Path
) -> None:
    project_dir = tmp_path / "demo"
    log = _previous_log(project_dir)
    host["runtime_executable"] = ""

    with pytest.raises(RuntimeError, match="Runtime executable path is missing"):
        launcher.launch("demo", {}, project_dir)

    assert log.read_text(encoding="utf-8") == "previous run log\n"


def test_a_restart_with_no_runtime_executable_leaves_the_runtime_running(
    host: dict[str, str], tmp_path: pathlib.Path
) -> None:
    project_dir = tmp_path / "demo"
    launcher.launch("demo", {}, project_dir)
    running = launcher._state["process"]
    assert running is not None and running.alive
    host["runtime_executable"] = ""  # edited after the launch

    with pytest.raises(RuntimeError, match="Runtime executable path is missing"):
        launcher.restart("demo", project_dir)

    assert running.alive, "the refused restart stopped the runtime first"
    assert launcher._state["process"] is running

    launcher.stop("demo")
