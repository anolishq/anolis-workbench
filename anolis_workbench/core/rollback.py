"""Rollback: thin wrapper over `install.sh --rollback`.

install.sh backs the prior binaries up to <prefix>/.prev on every install
and owns the restore (copy back, chown, restart, health check) — one
provisioning engine for deploy and rollback alike. Works locally or over
SSH via the Executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from anolis_workbench.core import deploy
from anolis_workbench.core.executor import Executor


@dataclass
class RollbackResult:
    """Result of a rollback operation."""

    success: bool
    output: str = ""
    error: str | None = None


def rollback(
    prefix: Path,
    *,
    executor: Executor | None = None,
) -> RollbackResult:
    """Restore the previous binaries via install.sh --rollback.

    Args:
        prefix: Install prefix (e.g. /opt/anolis).
        executor: Executor for the target (local default, or SSH).

    Returns:
        RollbackResult; failure details (e.g. no .prev backup) in .error.
    """
    try:
        output = deploy.run_rollback(executor, prefix=prefix)
    except deploy.DeployError as exc:
        return RollbackResult(success=False, error=str(exc))
    return RollbackResult(success=True, output=output)
