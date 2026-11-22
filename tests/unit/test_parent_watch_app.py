"""The server stops when the desktop app or its bootloader is gone, whatever its stdout does.

A process chain stands in for the desktop app: an app starts a bootloader
with its stdout piped back to the app, as the Tauri shell plugin does, and
the bootloader starts the server stand-in, which inherits that pipe. Found in
review of #916:
- On a quit the app kills the bootloader and exits, so the server's stdout
  pipe has no reader. The watch printed before it stopped the server, the
  print raised BrokenPipeError, and the server kept its port.
- An app crash leaves the bootloader alive, and the watch followed only the
  bootloader.
- A bootloader killed before the watch started left the server reparented to
  a process that never exits, which the watch then followed.
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

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the process chain relies on POSIX reparenting")

REPO = pathlib.Path(__file__).resolve().parents[2]

SERVER = textwrap.dedent(
    """
    import pathlib, sys, threading, time
    from anolis_workbench.server import parent_watch
    out = pathlib.Path(sys.argv[1])
    (out / "server.pid").write_text(str(__import__("os").getpid()))
    go = out / "watch.go"
    while not go.exists():
        time.sleep(0.02)
    done = threading.Event()
    parent_watch.watch(done.set, interval_s=0.05)
    (out / "server.watching").write_text("1")
    done.wait(60)
    (out / "server.stopped").write_text("1")
    """
)

BOOTLOADER = textwrap.dedent(
    """
    import pathlib, subprocess, sys, time
    out = pathlib.Path(sys.argv[2])
    subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]])
    (out / "boot.pid").write_text(str(__import__("os").getpid()))
    time.sleep(120)
    """
)

# The app: names itself in the environment, as main.rs does, and keeps the
# read end of the bootloader's stdout without reading it.
APP = textwrap.dedent(
    """
    import os, subprocess, sys, time
    env = dict(os.environ, ANOLIS_WORKBENCH_APP_PID=str(os.getpid()))
    subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2], sys.argv[3]], stdout=subprocess.PIPE, env=env)
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


def _kill(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass


@pytest.fixture()
def chain(tmp_path: pathlib.Path):
    env = dict(os.environ, PYTHONPATH=str(REPO))
    app = subprocess.Popen([sys.executable, "-c", APP, BOOTLOADER, SERVER, str(tmp_path)], env=env)
    assert _wait_for(lambda: (tmp_path / "server.pid").exists() and (tmp_path / "boot.pid").exists())
    boot_pid = int((tmp_path / "boot.pid").read_text())
    server_pid = int((tmp_path / "server.pid").read_text())
    yield app, boot_pid, server_pid
    for pid in (server_pid, boot_pid, app.pid):
        _kill(pid)
    app.wait(10)


def _start_watch(tmp_path: pathlib.Path) -> None:
    (tmp_path / "watch.go").write_text("", encoding="utf-8")


def _stopped(tmp_path: pathlib.Path) -> bool:
    return _wait_for(lambda: (tmp_path / "server.stopped").exists())


def test_a_quit_stops_the_server_though_its_stdout_has_no_reader(chain, tmp_path: pathlib.Path) -> None:
    app, boot_pid, _ = chain
    _start_watch(tmp_path)
    assert _wait_for(lambda: (tmp_path / "server.watching").exists())
    _kill(boot_pid)  # stop_sidecar
    app.kill()  # the app exits, and the read end of the server's stdout with it
    app.wait(10)
    assert _stopped(tmp_path), "the server outlived a quit"


def test_an_app_crash_stops_the_server(chain, tmp_path: pathlib.Path) -> None:
    app, _, _ = chain
    _start_watch(tmp_path)
    assert _wait_for(lambda: (tmp_path / "server.watching").exists())
    app.kill()  # the bootloader is still alive
    app.wait(10)
    assert _stopped(tmp_path), "the server outlived the app"


def test_a_bootloader_gone_before_the_watch_starts_stops_the_server(chain, tmp_path: pathlib.Path) -> None:
    _, boot_pid, server_pid = chain
    _kill(boot_pid)
    assert _wait_for(lambda: psutil.Process(server_pid).ppid() != boot_pid)
    _start_watch(tmp_path)
    assert _stopped(tmp_path), "the server outlived a bootloader that died before the watch began"


def test_the_server_keeps_running_while_app_and_bootloader_do(chain, tmp_path: pathlib.Path) -> None:
    _start_watch(tmp_path)
    assert _wait_for(lambda: (tmp_path / "server.watching").exists())
    time.sleep(0.5)
    assert not (tmp_path / "server.stopped").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="models psutil's Linux boot-time clock")
def test_a_wall_clock_step_does_not_stop_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #933: identity was create_time(), which psutil computes on
    Linux as start ticks plus the boot time it reads now. A wall-clock step
    (the first NTP sync after boot) moved it, and the watch stopped the
    server with the app alive. Modelled by shifting psutil's boot time."""
    import threading

    import psutil._pslinux as pslinux

    from anolis_workbench.server import parent_watch

    monkeypatch.delenv(parent_watch.APP_PID_ENV_VAR, raising=False)
    stopped = threading.Event()
    parent_watch.watch(stopped.set, interval_s=0.02)
    real_boot_time = pslinux.boot_time
    monkeypatch.setattr(pslinux, "boot_time", lambda: real_boot_time() + 1.0)
    assert not stopped.wait(0.5), "a clock step was taken for the parent exiting"


@pytest.mark.skipif(sys.platform != "linux", reason="models psutil's Linux boot-time clock")
def test_a_wall_clock_step_does_not_stop_the_server_with_an_app(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    import psutil._pslinux as pslinux

    from anolis_workbench.server import parent_watch

    # This test process's parent's parent stands in for the app.
    monkeypatch.setenv(parent_watch.APP_PID_ENV_VAR, str(psutil.Process(os.getppid()).ppid()))
    stopped = threading.Event()
    parent_watch.watch(stopped.set, interval_s=0.02)
    assert not stopped.wait(0.2), "the watch stopped before any clock step"
    real_boot_time = pslinux.boot_time
    monkeypatch.setattr(pslinux, "boot_time", lambda: real_boot_time() + 1.0)
    assert not stopped.wait(0.5), "a clock step was taken for the app exiting"
