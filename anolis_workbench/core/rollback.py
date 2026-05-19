"""Rollback support for Anolis provisioning.

Before upgrading, binaries are backed up to <name>.prev. On rollback,
the .prev copy is swapped back and the service restarted.

Only one level of rollback is supported (no history stack).
Rollback covers binaries only — configs are regenerated from template.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from anolis_workbench.core.executor import Executor, LocalExecutor

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RollbackResult:
    """Result of a rollback operation."""

    rolled_back: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    service_restarted: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def backup_binary(
    binary_name: str,
    prefix: Path,
    *,
    executor: Executor | None = None,
) -> bool:
    """Back up a binary to <name>.prev before overwriting.

    Args:
        binary_name: Name of the binary (e.g. "anolis-runtime").
        prefix: Install prefix (e.g. /usr/local).
        executor: Executor for I/O.

    Returns:
        True if backup was created, False if binary doesn't exist yet.
    """
    if executor is None:
        executor = LocalExecutor()

    binary_path = f"{prefix}/bin/{binary_name}"
    if not executor.file_exists(binary_path):
        return False

    result = executor.run(["cp", binary_path, f"{binary_path}.prev"], sudo=True)
    return result.returncode == 0


def backup_binaries(
    binary_names: list[str],
    prefix: Path,
    *,
    executor: Executor | None = None,
) -> list[str]:
    """Back up multiple binaries. Returns list of names that were backed up."""
    if executor is None:
        executor = LocalExecutor()

    backed_up: list[str] = []
    for name in binary_names:
        if backup_binary(name, prefix, executor=executor):
            backed_up.append(name)
    return backed_up


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def rollback_binary(
    binary_name: str,
    prefix: Path,
    *,
    executor: Executor | None = None,
) -> bool:
    """Rollback a single binary by swapping .prev back.

    Returns True if rollback succeeded, False if .prev doesn't exist.
    """
    if executor is None:
        executor = LocalExecutor()

    prev_path = f"{prefix}/bin/{binary_name}.prev"
    binary_path = f"{prefix}/bin/{binary_name}"

    if not executor.file_exists(prev_path):
        return False

    result = executor.run(["mv", prev_path, binary_path], sudo=True)
    return result.returncode == 0


def rollback(
    binary_names: list[str],
    prefix: Path,
    *,
    project_name: str | None = None,
    systemd: bool = False,
    executor: Executor | None = None,
) -> RollbackResult:
    """Roll back all specified binaries and optionally restart the service.

    Args:
        binary_names: List of binary names to roll back.
        prefix: Install prefix.
        project_name: Project name (for systemd restart).
        systemd: If True, restart the systemd service after rollback.
        executor: Executor for I/O.

    Returns:
        RollbackResult with details of what was rolled back.
    """
    if executor is None:
        executor = LocalExecutor()

    result = RollbackResult()

    for name in binary_names:
        prev_path = f"{prefix}/bin/{name}.prev"
        if not executor.file_exists(prev_path):
            result.failed.append(name)
            continue

        if rollback_binary(name, prefix, executor=executor):
            result.rolled_back.append(name)
        else:
            result.failed.append(name)

    if result.failed and not result.rolled_back:
        result.error = "No .prev backups found — cannot rollback (fresh install?)"
        return result

    # Restart service if requested
    if systemd and project_name:
        service_name = f"anolis-{project_name}.service"
        restart = executor.run(["systemctl", "restart", service_name], sudo=True)
        if restart.returncode == 0:
            result.service_restarted = True
        else:
            result.error = f"Rollback succeeded but service restart failed: {restart.stderr.strip()}"

    return result
