"""Executor abstraction for local vs remote (SSH) command execution.

Provides a uniform interface so installer logic works identically whether
targeting the local machine or a remote host over SSH.
"""

from __future__ import annotations

import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    """Result of executing a command."""

    returncode: int
    stdout: str
    stderr: str


class Executor(ABC):
    """Abstract base for command execution on a target machine."""

    @abstractmethod
    def run(self, cmd: list[str], *, input: bytes | None = None, sudo: bool = False) -> RunResult:
        """Run a command on the target. Returns RunResult."""

    @abstractmethod
    def write_file(self, path: str, data: bytes) -> None:
        """Write bytes to a file on the target."""

    @abstractmethod
    def mkdir(self, path: str) -> None:
        """Create directory (and parents) on the target."""

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Check if a file exists on the target."""


class LocalExecutor(Executor):
    """Executes operations on the local machine via subprocess + pathlib."""

    def run(self, cmd: list[str], *, input: bytes | None = None, sudo: bool = False) -> RunResult:
        full_cmd = (["sudo"] + cmd) if sudo else cmd
        result = subprocess.run(full_cmd, input=input, capture_output=True, timeout=30)
        return RunResult(
            returncode=result.returncode,
            stdout=result.stdout.decode(errors="replace"),
            stderr=result.stderr.decode(errors="replace"),
        )

    def write_file(self, path: str, data: bytes) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def mkdir(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def file_exists(self, path: str) -> bool:
        return Path(path).exists()


class SubprocessSSHExecutor(Executor):
    """Executes operations on a remote machine via system ssh/scp."""

    def __init__(self, host: str, user: str, *, key_file: str | None = None, port: int = 22):
        self.host = host
        self.user = user
        self.key_file = key_file
        self.port = port

    def _ssh_base(self, *, allocate_pty: bool = False) -> list[str]:
        args = ["ssh", "-o", "BatchMode=yes", "-p", str(self.port)]
        if self.key_file:
            args += ["-i", self.key_file]
        if allocate_pty:
            args += ["-t", "-t"]
        args.append(f"{self.user}@{self.host}")
        return args

    def run(self, cmd: list[str], *, input: bytes | None = None, sudo: bool = False) -> RunResult:
        remote_cmd = (["sudo"] + cmd) if sudo else cmd
        ssh_cmd = self._ssh_base(allocate_pty=sudo) + [shlex.join(remote_cmd)]
        result = subprocess.run(
            ssh_cmd,
            input=input,
            capture_output=True,
            timeout=60,
        )
        return RunResult(
            returncode=result.returncode,
            stdout=result.stdout.decode(errors="replace"),
            stderr=result.stderr.decode(errors="replace"),
        )

    def write_file(self, path: str, data: bytes) -> None:
        # Write via ssh cat > path
        ssh_cmd = self._ssh_base() + [f"cat > {shlex.quote(path)}"]
        result = subprocess.run(ssh_cmd, input=data, capture_output=True, timeout=60)
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            raise OSError(f"Failed to write {path} on remote: {stderr}")

    def mkdir(self, path: str) -> None:
        result = self.run(["mkdir", "-p", path])
        if result.returncode != 0:
            raise OSError(f"Failed to mkdir {path} on remote: {result.stderr}")

    def file_exists(self, path: str) -> bool:
        result = self.run(["test", "-e", path])
        return result.returncode == 0
