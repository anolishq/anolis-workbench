"""Stop the server when the process that started it is gone.

The desktop app starts the server as a PyInstaller onefile sidecar: a
bootloader whose child is this server. On quit the app kills the bootloader
only. Without this watch the server outlived it and kept the port (wb#667).
Killing the whole process group instead also killed any runtime the server
had launched, by SIGKILL, before its safe-state shutdown could run. A runtime
the workbench launched keeps running when the app quits; the next workbench
start finds it through running.json.

The desktop app turns the watch on with ANOLIS_WORKBENCH_EXIT_WITH_PARENT=1.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

import psutil

ENV_VAR = "ANOLIS_WORKBENCH_EXIT_WITH_PARENT"


def _parent_identity() -> tuple[int, float | None]:
    """The parent's pid and its start time, so a reused pid is not taken
    for the parent."""
    pid = os.getppid()
    try:
        return pid, psutil.Process(pid).create_time()
    except psutil.Error:
        return pid, None


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


def watch(stop: Callable[[], None], interval_s: float = 1.0) -> threading.Thread:
    """Call `stop` once the current parent process has exited."""
    pid, created = _parent_identity()

    def _run() -> None:
        while parent_alive(pid, created):
            time.sleep(interval_s)
        print("The desktop app that started this server has exited; stopping.", flush=True)
        stop()

    thread = threading.Thread(target=_run, name="parent-watch", daemon=True)
    thread.start()
    return thread


def enabled() -> bool:
    return os.environ.get(ENV_VAR) == "1"
