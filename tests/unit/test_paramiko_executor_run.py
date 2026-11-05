"""ParamikoSSHExecutor.run against a real paramiko SSH server (#656).

The server is paramiko's own, in this process, on one end of a socketpair;
the executor's SSHClient drives a Transport on the other end. No port is
opened and no host is contacted. Mocks cannot show this defect: it lives in
the SSH channel window and in paramiko's exit-status wait, so the test needs
the real protocol on both sides.

Every run() goes through _run_bounded, which joins the call's thread with a
deadline, so a regression fails the test instead of hanging the suite.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import paramiko
import pytest
from paramiko.common import AUTH_SUCCESSFUL, OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED, OPEN_SUCCEEDED

from anolis_workbench.core import deploy
from anolis_workbench.core.executor import ParamikoSSHExecutor, RunResult
from anolis_workbench.server.routes import provision

# paramiko's default channel window, which both stdout and stderr share.
WINDOW = paramiko.common.DEFAULT_WINDOW_SIZE
BIG = 8 * 1024 * 1024
assert BIG > 2 * WINDOW

# How long a bounded run() may take beyond the timeout it was given.
SLACK_S = 5.0

Behaviour = Callable[[paramiko.Channel], None]


@dataclass
class _Server(paramiko.ServerInterface):
    behaviour: Behaviour
    commands: list[str] = field(default_factory=list)
    channels: list[paramiko.Channel] = field(default_factory=list)

    def get_allowed_auths(self, username: str) -> str:
        return "none"

    def check_auth_none(self, username: str) -> int:
        return AUTH_SUCCESSFUL

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return OPEN_SUCCEEDED if kind == "session" else OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        self.commands.append(command.decode())
        self.channels.append(channel)
        threading.Thread(target=self._serve, args=(channel,), daemon=True).start()
        return True

    def _serve(self, channel: paramiko.Channel) -> None:
        # paramiko sends the exec reply only after this check returns, and a
        # channel the server closes before that reply makes the client's
        # exec_command raise "Channel closed". Give the reply a head start.
        time.sleep(0.3)
        try:
            self.behaviour(channel)
        except (OSError, EOFError, paramiko.SSHException):
            pass  # the client went away, as the timeout cases make it do


@pytest.fixture(scope="module")
def host_key() -> paramiko.RSAKey:
    return paramiko.RSAKey.generate(1024)


@dataclass
class _Link:
    executor: ParamikoSSHExecutor
    server: _Server


@pytest.fixture
def connect(host_key: paramiko.RSAKey) -> Iterator[Callable[[Behaviour], _Link]]:
    transports: list[paramiko.Transport] = []

    def _connect(behaviour: Behaviour) -> _Link:
        client_sock, server_sock = socket.socketpair()
        server = _Server(behaviour)
        server_transport = paramiko.Transport(server_sock)
        transports.append(server_transport)
        server_transport.add_server_key(host_key)
        # With an event, start_server returns at once; without one it would
        # wait for a client that has not started yet.
        server_transport.start_server(event=threading.Event(), server=server)
        client_transport = paramiko.Transport(client_sock)
        transports.append(client_transport)
        client_transport.start_client(timeout=10)
        client_transport.auth_none("pi")

        client = paramiko.SSHClient()
        # SSHClient.exec_command opens its session on this transport. Injecting
        # it stands in for connect(), which would need a listening server and
        # a known_hosts entry.
        client._transport = client_transport  # type: ignore[attr-defined]
        executor = ParamikoSSHExecutor.__new__(ParamikoSSHExecutor)
        executor.host, executor.user, executor.port = "127.0.0.1", "pi", 22
        executor._client = client
        executor._sftp = MagicMock()  # close() closes it; run() never uses it
        return _Link(executor, server)

    yield _connect
    for t in transports:
        t.close()


def _run_bounded(
    executor: ParamikoSSHExecutor, cmd: list[str], *, timeout: float, join_s: float, input: bytes | None = None
) -> RunResult:
    """Call run() in a thread; fail if it is still running after join_s seconds."""
    outcome: dict[str, object] = {}

    def _call() -> None:
        try:
            outcome["result"] = executor.run(cmd, input=input, timeout=timeout)
        except BaseException as exc:  # noqa: BLE001 — re-raised in the test thread
            outcome["error"] = exc

    thread = threading.Thread(target=_call, daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(join_s)
    if thread.is_alive():
        pytest.fail(f"run(timeout={timeout}) still blocked after {time.monotonic() - started:.1f}s")
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["result"]  # type: ignore[return-value]


def _finish(channel: paramiko.Channel, status: int) -> None:
    channel.send_exit_status(status)
    channel.shutdown_write()
    channel.close()


def test_small_output_returns_status_and_both_streams(connect: Callable[[Behaviour], _Link]) -> None:
    def behaviour(channel: paramiko.Channel) -> None:
        channel.sendall(b"x86_64\n")
        channel.sendall_stderr(b"a warning\n")
        _finish(channel, 3)

    link = connect(behaviour)
    result = _run_bounded(link.executor, ["uname", "-m"], timeout=5, join_s=5 + SLACK_S)

    assert (result.returncode, result.stdout, result.stderr) == (3, "x86_64\n", "a warning\n")
    assert link.server.commands == ["uname -m"]


def test_stdout_larger_than_the_channel_window_does_not_deadlock(connect: Callable[[Behaviour], _Link]) -> None:
    def behaviour(channel: paramiko.Channel) -> None:
        channel.sendall(b"o" * BIG)
        _finish(channel, 0)

    link = connect(behaviour)
    result = _run_bounded(link.executor, ["cat", "/x"], timeout=10, join_s=10 + SLACK_S)

    assert result.returncode == 0
    assert len(result.stdout) == BIG


def test_stderr_and_stdout_are_drained_together(connect: Callable[[Behaviour], _Link]) -> None:
    # The window is per channel, so stderr filling it stalls stdout too: a
    # reader that takes stdout to EOF before it touches stderr deadlocks here.
    def behaviour(channel: paramiko.Channel) -> None:
        channel.sendall_stderr(b"e" * BIG)
        channel.sendall(b"o" * BIG)
        _finish(channel, 0)

    link = connect(behaviour)
    result = _run_bounded(link.executor, ["cat", "/x"], timeout=10, join_s=10 + SLACK_S)

    assert (len(result.stdout), len(result.stderr)) == (BIG, BIG)


def test_output_sent_after_the_exit_status_is_kept(connect: Callable[[Behaviour], _Link]) -> None:
    # A server may send exit-status before the last of the output; the output
    # ends at EOF, not at the status.
    # The stderr line, which follows the status, wakes the reader while the
    # status is in and the stdout tail is not.
    def behaviour(channel: paramiko.Channel) -> None:
        channel.send_exit_status(0)
        channel.sendall_stderr(b"after status\n")
        time.sleep(0.5)
        channel.sendall(b"tail\n")
        channel.shutdown_write()
        channel.close()

    link = connect(behaviour)
    result = _run_bounded(link.executor, ["cat", "/x"], timeout=5, join_s=5 + SLACK_S)

    assert (result.returncode, result.stdout, result.stderr) == (0, "tail\n", "after status\n")


def test_input_reaches_the_command_and_its_reply_is_read(connect: Callable[[Behaviour], _Link]) -> None:
    def behaviour(channel: paramiko.Channel) -> None:
        received = bytearray()
        while chunk := channel.recv(32768):
            received += chunk
        channel.sendall(bytes(reversed(received)))
        _finish(channel, 0)

    link = connect(behaviour)
    result = _run_bounded(link.executor, ["cat"], input=b"hello", timeout=5, join_s=5 + SLACK_S)

    assert result.stdout == "olleh"


def _never_exits(channel: paramiko.Channel) -> None:
    channel.sendall(b"started\n")
    while not channel.closed:
        time.sleep(0.05)


def _eof_but_no_status(channel: paramiko.Channel) -> None:
    channel.sendall(b"started\n")
    channel.shutdown_write()
    while not channel.closed:
        time.sleep(0.05)


@pytest.mark.parametrize("behaviour", [_never_exits, _eof_but_no_status], ids=["never-exits", "eof-no-status"])
def test_timeout_raises_and_closes_the_channel(connect: Callable[[Behaviour], _Link], behaviour: Behaviour) -> None:
    link = connect(behaviour)
    timeout = 1.0
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        _run_bounded(link.executor, ["bash", "/tmp/install.sh"], timeout=timeout, join_s=timeout + SLACK_S)

    elapsed = time.monotonic() - started
    assert timeout <= elapsed < timeout + SLACK_S
    assert raised.value.timeout == timeout
    assert raised.value.output == b"started\n"
    assert "timed out after 1.0 seconds" in str(raised.value)
    # The executor closed its end: the server sees its channel close.
    (channel,) = link.server.channels
    deadline = time.monotonic() + SLACK_S
    while not channel.closed:
        assert time.monotonic() < deadline, "the server's channel is still open"
        time.sleep(0.05)


def test_a_hung_install_ends_the_remote_provision_job_failed(
    connect: Callable[[Behaviour], _Link], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The provision thread's install.sh call is bounded by INSTALL_TIMEOUT_S:
    when it expires the job ends `failed`, says why, and closes the executor."""
    link = connect(_never_exits)
    closed = MagicMock(wraps=link.executor.close)
    monkeypatch.setattr(link.executor, "close", closed)
    monkeypatch.setattr(deploy, "INSTALL_TIMEOUT_S", 1.0)
    monkeypatch.setattr(provision, "ParamikoSSHExecutor", lambda **kwargs: link.executor)
    monkeypatch.setattr(provision, "_prepare_workspace", lambda params, progress: tmp_path)

    def _deploy_remote(*, executor: ParamikoSSHExecutor, **kwargs: Any) -> deploy.DeployResult:
        deploy._run_install_sh(executor, "/tmp/install.sh", ["--project", "/tmp/rig"], "rig", None)
        raise AssertionError("install.sh returned")

    monkeypatch.setattr(deploy, "deploy_remote", _deploy_remote)
    job = provision.ProvisionJob(job_id="hung")
    thread = threading.Thread(
        target=provision._run_remote_job, args=(job, {"project": "rig", "target": "pi@rig"}), daemon=True
    )
    thread.start()
    thread.join(1.0 + SLACK_S)

    assert not thread.is_alive(), "the provision thread is still blocked"
    assert job.status == "failed"
    assert job.error == "Command 'sudo bash /tmp/install.sh --project /tmp/rig' timed out after 1.0 seconds"
    assert job.events[-1]["stage"] == "error"
    closed.assert_called_once_with()
