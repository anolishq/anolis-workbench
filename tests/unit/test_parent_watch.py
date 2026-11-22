"""The server stops when the desktop app's sidecar bootloader is gone, and a
runtime it launched keeps running (wb#667, and the review of #892).

A real process chain stands in for the desktop app: a parent (the
bootloader) starts a server stand-in that runs parent_watch.watch, and that
stand-in starts a long-running child (the runtime). Killing the parent, as
the desktop app's kill() does, must stop the stand-in and leave its child.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap
import time

import psutil
import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

SERVER = textwrap.dedent(
    """
    import pathlib, subprocess, sys, threading
    from anolis_workbench.server import parent_watch
    out = pathlib.Path(sys.argv[1])
    runtime = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    (out / "runtime.pid").write_text(str(runtime.pid))
    done = threading.Event()
    parent_watch.watch(done.set, interval_s=0.05)
    (out / "server.ready").write_text("1")
    done.wait(60)
    (out / "server.stopped").write_text("1")
    """
)

BOOTLOADER = textwrap.dedent(
    """
    import subprocess, sys, time
    server = subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]])
    print(server.pid, flush=True)
    time.sleep(120)
    """
)


def _wait_for(predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def chain(tmp_path: pathlib.Path):
    env = dict(os.environ, PYTHONPATH=str(REPO))
    boot = subprocess.Popen(
        [sys.executable, "-c", BOOTLOADER, SERVER, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert boot.stdout is not None
    server_pid = int(boot.stdout.readline())
    assert _wait_for(lambda: (tmp_path / "server.ready").exists()), "the server stand-in never started its watch"
    runtime_pid = int((tmp_path / "runtime.pid").read_text())
    yield boot, server_pid, runtime_pid
    for pid in (runtime_pid, server_pid, boot.pid):
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass
    boot.wait(10)


def test_the_server_stops_when_its_parent_is_killed(chain, tmp_path: pathlib.Path) -> None:
    boot, server_pid, _ = chain
    boot.kill()
    boot.wait(10)
    assert _wait_for(lambda: (tmp_path / "server.stopped").exists()), "the server outlived its parent"
    assert _wait_for(
        lambda: not psutil.pid_exists(server_pid) or psutil.Process(server_pid).status() == psutil.STATUS_ZOMBIE
    )


def test_a_runtime_the_server_launched_keeps_running(chain, tmp_path: pathlib.Path) -> None:
    boot, _, runtime_pid = chain
    boot.kill()
    boot.wait(10)
    assert _wait_for(lambda: (tmp_path / "server.stopped").exists())
    time.sleep(0.5)
    assert psutil.Process(runtime_pid).is_running()
    assert psutil.Process(runtime_pid).status() != psutil.STATUS_ZOMBIE


def test_the_server_keeps_running_while_its_parent_does(chain, tmp_path: pathlib.Path) -> None:
    time.sleep(0.5)
    assert not (tmp_path / "server.stopped").exists()


def test_the_watch_is_off_unless_the_desktop_app_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    from anolis_workbench.server import parent_watch

    monkeypatch.delenv(parent_watch.ENV_VAR, raising=False)
    assert not parent_watch.enabled()
    monkeypatch.setenv(parent_watch.ENV_VAR, "1")
    assert parent_watch.enabled()
