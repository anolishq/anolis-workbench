"""A runtime the workbench launched keeps a working stdout and stderr after the server is gone.

The desktop app leaves a runtime it launched running when it quits (#916). The
runtime's output was a pipe the server read, so once the server exited nothing
read it. The runtime survived that, but its providers inherit its stderr and
the default SIGPIPE, so a C++ provider died on its next log line. The runtime
now writes to its log file directly, and the server follows that file.
"""

from __future__ import annotations

import os
import pathlib
import sys
import textwrap
import time

import pytest

from anolis_workbench.core import launcher

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in runtime is a POSIX script")

# Writes a line, waits for the test to take the server's side away, writes
# again, and exits 0 only if every write succeeded.
_STAND_IN = textwrap.dedent(
    """\
    #!{python}
    import pathlib, sys, time
    go = pathlib.Path({go!r})
    print("runtime started", flush=True)
    deadline = time.monotonic() + 10
    while not go.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        for i in range(3):
            print(f"after the server: line {{i}}", flush=True)
            print(f"stderr line {{i}}", file=sys.stderr, flush=True)
    except BrokenPipeError:
        sys.exit(3)
    """
)


@pytest.fixture
def project(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    go = tmp_path / "go"
    exe = tmp_path / "anolis-runtime"
    exe.write_text(_STAND_IN.format(python=sys.executable, go=str(go)), encoding="utf-8")
    exe.chmod(0o755)
    config = tmp_path / "launch.yaml"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("anolis_workbench.core.paths.DATA_ROOT", tmp_path)
    monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", tmp_path / "systems")
    monkeypatch.setattr(launcher, "materialize_launch_config", lambda project_dir, system: config)
    monkeypatch.setattr("anolis_workbench.core.canonical_validator.validate_project", lambda system: [])
    monkeypatch.setattr("anolis_workbench.core.projects.get_project", lambda name: {})
    monkeypatch.setattr(launcher.canonical, "host_paths", lambda doc: {"runtime_executable": str(exe)})
    monkeypatch.setattr(launcher, "_discover_running_runtime", lambda clean_stale=False: None)
    monkeypatch.setattr(launcher, "_discover_external_runtime", lambda: None)
    monkeypatch.setattr(launcher, "_state", dict(launcher._state, process=None, project=None, log_lines_by_project={}))
    return tmp_path / "systems" / "demo"


def test_the_runtime_keeps_writing_after_the_server_stops_reading(
    project: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The server is gone: nothing follows the runtime's output, and the
    # server's end of any pipe is closed.
    monkeypatch.setattr(launcher, "_read_logs", lambda proc, log, name: None)
    launcher.launch("demo", {}, project)
    proc = launcher._state["process"]
    if proc.stdout is not None:
        proc.stdout.close()

    (project.parent.parent / "go").write_text("", encoding="utf-8")

    assert proc.wait(timeout=10) == 0, "the runtime's writes failed once the server was gone"
    log = (project / "logs" / "latest.log").read_text(encoding="utf-8")
    assert "after the server: line 2" in log
    assert "stderr line 2" in log


def test_the_server_follows_the_runtime_log(project: pathlib.Path) -> None:
    launcher.launch("demo", {}, project)
    proc = launcher._state["process"]
    (project.parent.parent / "go").write_text("", encoding="utf-8")
    proc.wait(timeout=10)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with launcher._state_lock:
            lines = list(launcher._state["log_lines_by_project"].get("demo", []))
        if any("line 2" in line for line in lines) and launcher._state["process"] is None:
            break
        time.sleep(0.05)
    assert lines[0] == "runtime started\n"
    assert "after the server: line 2\n" in lines
    assert launcher._state["process"] is None, "the follower cleans up once the runtime has exited"


def test_a_failed_spawn_keeps_the_previous_log(project: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = project / "logs" / "latest.log"
    log.parent.mkdir(parents=True)
    log.write_text("previous run log\n", encoding="utf-8")

    def _popen(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", "/nowhere")

    monkeypatch.setattr(launcher.subprocess, "Popen", _popen)
    with pytest.raises(FileNotFoundError):
        launcher.launch("demo", {}, project)
    assert log.read_text(encoding="utf-8") == "previous run log\n"


def _logs(project: pathlib.Path) -> dict[str, str]:
    logs = project / "logs"
    return {p.name: p.read_text("utf-8") for p in sorted(logs.iterdir())} if logs.exists() else {}


def test_a_failed_spawn_keeps_both_logs(project: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #932: a failed launch kept latest.log but lost previous.log."""
    (project / "logs").mkdir(parents=True)
    (project / "logs" / "latest.log").write_text("run A\n", encoding="utf-8")
    (project / "logs" / "previous.log").write_text("run B\n", encoding="utf-8")

    def _popen(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", "/nowhere")

    monkeypatch.setattr(launcher.subprocess, "Popen", _popen)
    with pytest.raises(FileNotFoundError):
        launcher.launch("demo", {}, project)
    assert _logs(project) == {"latest.log": "run A\n", "previous.log": "run B\n"}


def test_a_launch_moves_the_last_log_to_previous(project: pathlib.Path) -> None:
    (project / "logs").mkdir(parents=True)
    (project / "logs" / "latest.log").write_text("run A\n", encoding="utf-8")
    (project / "logs" / "previous.log").write_text("run B\n", encoding="utf-8")
    launcher.launch("demo", {}, project)
    proc = launcher._state["process"]
    (project.parent.parent / "go").write_text("", encoding="utf-8")
    proc.wait(timeout=10)
    logs = _logs(project)
    assert logs["previous.log"] == "run A\n"
    assert logs["latest.log"].startswith("runtime started\n")
    assert set(logs) == {"latest.log", "previous.log"}


@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc/<pid>/fdinfo")
def test_the_runtime_log_is_opened_for_append(project: pathlib.Path) -> None:
    """Review of #932: without O_APPEND, a truncation under a live writer left
    a file of NULs up to the writer's old offset."""
    launcher.launch("demo", {}, project)
    proc = launcher._state["process"]
    try:
        for fd in (1, 2):
            flags = pathlib.Path(f"/proc/{proc.pid}/fdinfo/{fd}").read_text("utf-8").split("flags:")[1].split()[0]
            assert int(flags, 8) & os.O_APPEND, f"fd {fd} flags {flags}"
    finally:
        (project.parent.parent / "go").write_text("", encoding="utf-8")
        proc.wait(timeout=10)


def test_a_server_that_finds_a_running_runtime_follows_its_log(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #932: only the server that launched a runtime followed its
    log. After a quit and reopen, the new server found the runtime through
    running.json and showed an empty log pane."""
    import json
    import subprocess

    systems = tmp_path / "systems"
    project_dir = systems / "demo"
    (project_dir / "logs").mkdir(parents=True)
    log = project_dir / "logs" / "latest.log"
    go = tmp_path / "go"
    script = (
        "import pathlib, sys, time\n"
        "print('written before the reopen', flush=True)\n"
        f"go = pathlib.Path({str(go)!r})\n"
        "while not go.exists(): time.sleep(0.02)\n"
        "print('written after the reopen', flush=True)\n"
        "time.sleep(0.5)\n"
    )
    with open(log, "wb") as out:
        runtime = subprocess.Popen([sys.executable, "-c", script], stdout=out, stderr=subprocess.STDOUT)
    try:
        (project_dir / "running.json").write_text(json.dumps({"pid": runtime.pid, "project": "demo"}), "utf-8")
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems)
        monkeypatch.setattr(launcher, "_discover_external_runtime", lambda: None)
        monkeypatch.setattr(
            launcher, "_state", dict(launcher._state, process=None, project=None, log_lines_by_project={})
        )
        deadline = time.monotonic() + 5
        while "written before" not in log.read_text("utf-8") and time.monotonic() < deadline:
            time.sleep(0.02)

        status = launcher.get_status()  # what the reopened app asks first
        assert status["running"] and status["active_project"] == "demo"
        go.write_text("", encoding="utf-8")

        def lines() -> list[str]:
            with launcher._state_lock:
                return list(launcher._state["log_lines_by_project"].get("demo", []))

        deadline = time.monotonic() + 5
        while "written after the reopen\n" not in lines() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "written before the reopen\n" in lines()
        assert "written after the reopen\n" in lines()
    finally:
        runtime.wait(10)


def test_the_runtime_is_in_its_own_session(project: pathlib.Path) -> None:
    """Review of #932: the runtime shared the server's process group, so a
    Ctrl-C in the terminal that started the app or server reached it too."""
    launcher.launch("demo", {}, project)
    proc = launcher._state["process"]
    try:
        assert os.getsid(proc.pid) == proc.pid
        assert os.getpgid(proc.pid) != os.getpgid(0)
    finally:
        (project.parent.parent / "go").write_text("", encoding="utf-8")
        proc.wait(timeout=10)


def test_keeping_the_old_log_needs_no_copy(project: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #944: a restart copied the whole old log after stopping the
    runtime, so a full disk left the machine stopped, and a large log made
    the restart slow. Setting the old log aside must not copy it."""
    (project / "logs").mkdir(parents=True)
    (project / "logs" / "latest.log").write_text("run A\n", encoding="utf-8")

    def _no_space(*args: object, **kwargs: object) -> None:
        raise OSError(27, "File too large")

    import shutil

    monkeypatch.setattr(shutil, "copyfile", _no_space)
    launcher.launch("demo", {}, project)
    proc = launcher._state["process"]
    (project.parent.parent / "go").write_text("", encoding="utf-8")
    proc.wait(timeout=10)
    logs = _logs(project)
    assert logs["previous.log"] == "run A\n"
    assert logs["latest.log"].startswith("runtime started\n")


def test_a_log_that_cannot_be_set_aside_does_not_stop_the_launch(
    project: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the old log cannot be moved (Windows, while a follower still holds
    it), the launch goes on and the old log is lost, not the machine."""
    (project / "logs").mkdir(parents=True)
    (project / "logs" / "latest.log").write_text("run A\n", encoding="utf-8")
    real_replace = launcher.os.replace

    def _held(src: object, dst: object) -> None:
        if str(src).endswith("latest.log"):
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(launcher.os, "replace", _held)
    launcher.launch("demo", {}, project)
    proc = launcher._state["process"]
    (project.parent.parent / "go").write_text("", encoding="utf-8")
    assert proc.wait(timeout=10) == 0
    assert _logs(project)["latest.log"].startswith("runtime started\n")


def test_a_log_left_aside_by_a_crash_is_recovered(project: pathlib.Path) -> None:
    """Review of #944: a .kept file with no latest.log was never cleaned up."""
    (project / "logs").mkdir(parents=True)
    (project / "logs" / "latest.log.kept").write_text("run A\n", encoding="utf-8")
    launcher.launch("demo", {}, project)
    proc = launcher._state["process"]
    (project.parent.parent / "go").write_text("", encoding="utf-8")
    proc.wait(timeout=10)
    logs = _logs(project)
    assert "latest.log.kept" not in logs
    assert logs["previous.log"] == "run A\n"


def test_the_runtime_is_tracked_before_running_json_names_it(
    project: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #942: running.json was written before the launcher tracked
    the process, so a status poll in between took the runtime for one found
    on disk and started a second follower: every line was then published
    twice."""
    seen: list[object] = []
    real_write_text = pathlib.Path.write_text

    def _record(self: pathlib.Path, *args: object, **kwargs: object) -> int:
        if self.name == "running.json":
            seen.append(launcher._state["process"])
        return real_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pathlib.Path, "write_text", _record)
    launcher.launch("demo", {}, project)
    proc = launcher._state["process"]
    (project.parent.parent / "go").write_text("", encoding="utf-8")
    proc.wait(timeout=10)
    assert seen == [proc], "running.json was written before the launcher tracked the runtime"
