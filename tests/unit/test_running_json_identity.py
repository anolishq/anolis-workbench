"""running.json names a runtime by its pid and its start, so a reused pid is not taken for it (#938).

A server that finds a runtime through running.json reports it, follows its
log and can stop it. With the pid alone, a reused pid showed as the running
runtime, its follower showed the dead run's log as live, and Stop ended an
unrelated process (review of #942).
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest

from anolis_workbench.core import launcher

pytestmark = pytest.mark.skipif(os.name == "nt", reason="uses POSIX processes as stand-ins")


@pytest.fixture
def systems(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = tmp_path / "systems"
    (root / "rig").mkdir(parents=True)
    monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", root)
    monkeypatch.setattr(launcher, "_discover_external_runtime", lambda: None)
    monkeypatch.setattr(launcher, "_state", dict(launcher._state, process=None, project=None, log_lines_by_project={}))
    monkeypatch.setattr(launcher, "_follow_detached", lambda *args, **kwargs: None)
    return root


@pytest.fixture
def unrelated() -> Iterator[subprocess.Popen]:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait(10)


def _write_running(systems: pathlib.Path, data: dict) -> pathlib.Path:
    path = systems / "rig" / "running.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_reused_pid_is_not_the_runtime(systems: pathlib.Path, unrelated: subprocess.Popen) -> None:
    # The runtime that wrote this has exited; its pid now belongs to another process.
    path = _write_running(systems, {"pid": unrelated.pid, "project": "rig", "start_marker": "0"})
    status = launcher.get_status()
    assert not status["running"]
    assert not path.exists(), "a stale running.json is cleaned up"


def test_stop_never_ends_a_process_with_a_reused_pid(systems: pathlib.Path, unrelated: subprocess.Popen) -> None:
    _write_running(systems, {"pid": unrelated.pid, "project": "rig", "start_marker": "0"})
    launcher.stop("rig")
    time.sleep(0.2)
    assert unrelated.poll() is None, "Stop ended an unrelated process"


def test_the_runtime_that_wrote_running_json_is_found(systems: pathlib.Path, unrelated: subprocess.Popen) -> None:
    marker = launcher._start_marker(unrelated.pid)
    assert marker is not None
    _write_running(systems, {"pid": unrelated.pid, "project": "rig", "start_marker": marker})
    status = launcher.get_status()
    assert status["running"] and status["active_project"] == "rig"


def test_a_running_json_without_a_start_is_reported_but_not_stopped(
    systems: pathlib.Path, unrelated: subprocess.Popen
) -> None:
    """Written by a workbench before this change: the process cannot be
    confirmed, so Stop refuses rather than signal it."""
    _write_running(systems, {"pid": unrelated.pid, "project": "rig"})
    assert launcher.get_status()["running"]
    with pytest.raises(RuntimeError, match=str(unrelated.pid)):
        launcher.stop("rig")
    assert unrelated.poll() is None
