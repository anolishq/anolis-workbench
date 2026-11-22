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


# A process is held as a psutil.Process, and asked is_running(): psutil
# compares its boot-relative start time, which a wall-clock step does not
# move. create_time() adds the boot time read now, so the first NTP sync after
# boot made a live app look gone (review of #933).


def _process(pid: int) -> psutil.Process | None:
    try:
        return psutil.Process(pid)
    except psutil.Error:
        return None


def _alive(proc: psutil.Process | None) -> bool:
    if proc is None:
        return False
    try:
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def parent_alive(parent: psutil.Process | None) -> bool:
    if parent is None:
        return False
    if os.name != "nt" and os.getppid() != parent.pid:
        # POSIX reparents an orphan, so a changed ppid means the parent died.
        return False
    return _alive(parent)


def _app_process(parent: psutil.Process | None) -> tuple[bool, psutil.Process | None]:
    """Whether the app named itself, and the app's process.

    The process is None when the app is already gone, or when this process's
    parent is not the app's child: the bootloader died before the watch began
    and the server was reparented. The watch stops at once then.
    """
    raw = os.environ.get(APP_PID_ENV_VAR, "")
    if not raw.isdigit():
        return False, None
    app_pid = int(raw)
    try:
        if parent is None or parent.ppid() != app_pid:
            return True, None
    except psutil.Error:
        return True, None
    return True, _process(app_pid)


def watch(stop: Callable[[], None], interval_s: float = 1.0) -> threading.Thread:
    """Call `stop` once the parent process, or the app that started it, has exited."""
    parent = _process(os.getppid())
    app_named, app = _app_process(parent)

    def _both_alive() -> bool:
        if not parent_alive(parent):
            return False
        return not app_named or _alive(app)

    def _run() -> None:
        while _both_alive():
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
