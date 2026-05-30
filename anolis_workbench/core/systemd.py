"""Systemd service management for Anolis runtime.

Generates unit files and installs/manages systemd services for Anolis projects.
Uses the Executor abstraction — works identically for local and remote targets.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

from anolis_workbench.core.executor import Executor, LocalExecutor
from anolis_workbench.core.paths import DEFAULT_INSTALL_PREFIX

_UNIT_TEMPLATE = dedent("""\
    [Unit]
    Description=Anolis Runtime — {project_name}
    After=network.target

    [Service]
    Type=simple
    User={user}
    WorkingDirectory={working_directory}
    ExecStart={exec_start}
    Restart=on-failure
    RestartSec=5s
    StandardOutput=journal
    StandardError=journal

    [Install]
    WantedBy=multi-user.target
""")

_SYSTEMD_UNIT_DIR = "/etc/systemd/system"


@dataclass
class SystemdResult:
    """Result of a systemd service install/start operation."""

    service_name: str
    installed: bool
    enabled: bool
    started: bool
    error: str | None = None


def service_name(project_name: str) -> str:
    """Get the systemd service name for a project."""
    return f"anolis-{project_name}.service"


def render_unit_file(
    project_name: str,
    *,
    install_prefix: Path = DEFAULT_INSTALL_PREFIX,
    systems_root: Path,
    user: str,
) -> str:
    """Render a systemd unit file for the given project.

    Args:
        project_name: Name of the project (e.g. "bioreactor-v1").
        install_prefix: Where binaries are installed.
        systems_root: Where project configs live.
        user: System user to run the service as.

    Returns:
        Complete unit file content as string.
    """
    working_directory = str(systems_root / project_name)
    config_path = str(systems_root / project_name / "anolis-runtime.yaml")
    exec_start = f"{install_prefix}/bin/anolis-runtime --config {config_path}"

    return _UNIT_TEMPLATE.format(
        project_name=project_name,
        user=user,
        working_directory=working_directory,
        exec_start=exec_start,
    )


def install_service(
    project_name: str,
    *,
    executor: Executor | None = None,
    install_prefix: Path = DEFAULT_INSTALL_PREFIX,
    systems_root: Path,
    user: str,
) -> SystemdResult:
    """Install and enable a systemd service for the project.

    Steps:
    1. Write unit file to /tmp
    2. sudo mv to /etc/systemd/system/
    3. sudo systemctl daemon-reload
    4. sudo systemctl enable <service>
    5. sudo systemctl start (or restart if already exists)

    Args:
        project_name: Name of the project.
        executor: Executor for I/O operations. Defaults to LocalExecutor.
        install_prefix: Where binaries are installed.
        systems_root: Where project configs live.
        user: System user to run the service as.

    Returns:
        SystemdResult with operation outcome.
    """
    if executor is None:
        executor = LocalExecutor()

    svc_name = service_name(project_name)
    unit_content = render_unit_file(
        project_name,
        install_prefix=install_prefix,
        systems_root=systems_root,
        user=user,
    )

    # Write to temp location first
    tmp_path = f"/tmp/{svc_name}"
    executor.write_file(tmp_path, unit_content.encode())

    # Move to systemd directory (requires sudo)
    dest_path = f"{_SYSTEMD_UNIT_DIR}/{svc_name}"

    # Check if service already exists (for restart vs start)
    existing = executor.file_exists(dest_path)

    result = executor.run(["mv", tmp_path, dest_path], sudo=True)
    if result.returncode != 0:
        return SystemdResult(
            service_name=svc_name,
            installed=False,
            enabled=False,
            started=False,
            error=f"Failed to install unit file: {result.stderr.strip()}",
        )

    # daemon-reload
    result = executor.run(["systemctl", "daemon-reload"], sudo=True)
    if result.returncode != 0:
        return SystemdResult(
            service_name=svc_name,
            installed=True,
            enabled=False,
            started=False,
            error=f"daemon-reload failed: {result.stderr.strip()}",
        )

    # enable
    result = executor.run(["systemctl", "enable", svc_name], sudo=True)
    if result.returncode != 0:
        return SystemdResult(
            service_name=svc_name,
            installed=True,
            enabled=False,
            started=False,
            error=f"enable failed: {result.stderr.strip()}",
        )

    # start or restart
    action = "restart" if existing else "start"
    result = executor.run(["systemctl", action, svc_name], sudo=True)
    if result.returncode != 0:
        return SystemdResult(
            service_name=svc_name,
            installed=True,
            enabled=True,
            started=False,
            error=f"{action} failed: {result.stderr.strip()}",
        )

    return SystemdResult(
        service_name=svc_name,
        installed=True,
        enabled=True,
        started=True,
    )


def wait_ready(
    executor: Executor | None = None,
    *,
    port: int = 8080,
    timeout_seconds: int = 30,
    poll_interval: int = 2,
) -> bool:
    """Poll the runtime health endpoint until ready or timeout.

    Args:
        executor: Executor to run curl on the target.
        port: Port the runtime listens on.
        timeout_seconds: Max time to wait.
        poll_interval: Seconds between polls.

    Returns:
        True if runtime became ready, False if timed out.
    """
    if executor is None:
        executor = LocalExecutor()

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = executor.run(["curl", "-sf", f"http://127.0.0.1:{port}/v0/status"])
        if result.returncode == 0:
            return True
        time.sleep(poll_interval)
    return False
