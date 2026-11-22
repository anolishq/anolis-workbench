"""Stop the server when the process that started it is gone.

The desktop app starts the server as a PyInstaller onefile sidecar: a
bootloader whose child is this server. On quit the app kills the bootloader
only. Without this watch the server outlived it and kept the port (wb#667).
Killing the whole process group instead also killed any runtime the server
had launched, by SIGKILL, before its safe-state shutdown could run. A runtime
the workbench launched keeps running when the app quits; the next workbench
start finds it through running.json.

The desktop app turns the watch on with ANOLIS_WORKBENCH_EXIT_WITH_PARENT=1
and names itself in ANOLIS_WORKBENCH_APP_PID. With the app's pid the server
also stops when the app is gone and the bootloader is not (an app crash), and
when the bootloader died before the watch began: the server's parent is the
bootloader only while that parent's own parent is the app.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

import psutil

ENV_VAR = "ANOLIS_WORKBENCH_EXIT_WITH_PARENT"
APP_PID_ENV_VAR = "ANOLIS_WORKBENCH_APP_PID"


def _parent_identity() -> tuple[int, float | None]:
    """The parent's pid and its start time, so a reused pid is not taken
    for the parent."""
    pid = os.getppid()
    try:
        return pid, psutil.Process(pid).create_time()
    except psutil.Error:
        return pid, None


def process_alive(pid: int, created: float) -> bool:
    """Whether `pid` is still the process that started at `created`."""
    try:
        proc = psutil.Process(pid)
        return proc.create_time() == created and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def parent_alive(pid: int, created: float | None) -> bool:
    if os.name != "nt" and os.getppid() != pid:
        # POSIX reparents an orphan, so a changed ppid means the parent died.
        return False
    try:
        proc = psutil.Process(pid)
        if created is not None and proc.create_time() != created:
            return False
        return proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _app_identity(parent_pid: int) -> tuple[int, float | None] | None:
    """The app's pid and start time, or None when the app did not name itself.

    The start time is None when the app is already gone, or when this
    process's parent is not the app's child: the bootloader died before the
    watch began and the server was reparented. The watch stops at once then.
    """
    raw = os.environ.get(APP_PID_ENV_VAR, "")
    if not raw.isdigit():
        return None
    app_pid = int(raw)
    try:
        if psutil.Process(parent_pid).ppid() != app_pid:
            return app_pid, None
        return app_pid, psutil.Process(app_pid).create_time()
    except psutil.Error:
        return app_pid, None


def watch(stop: Callable[[], None], interval_s: float = 1.0) -> threading.Thread:
    """Call `stop` once the parent process, or the app that started it, has exited."""
    pid, created = _parent_identity()
    app = _app_identity(pid)

    def _alive() -> bool:
        if not parent_alive(pid, created):
            return False
        if app is None:
            return True
        app_pid, app_created = app
        return app_created is not None and process_alive(app_pid, app_created)

    def _run() -> None:
        while _alive():
            time.sleep(interval_s)
        # Stop first: on a quit nothing reads this process's stdout any more,
        # and a print that raised used to end the thread before the stop.
        stop()
        try:
            print("The desktop app that started this server has exited; stopping.", flush=True)
        except (OSError, ValueError):
            pass

    thread = threading.Thread(target=_run, name="parent-watch", daemon=True)
    thread.start()
    return thread


def enabled() -> bool:
    return os.environ.get(ENV_VAR) == "1"
