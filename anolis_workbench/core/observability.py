"""Observability stack deployment for Anolis provisioning.

Handles downloading, extracting, and optionally starting the Docker Compose
observability stack (InfluxDB + Grafana) from the runtime release assets.
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from anolis_workbench.core.executor import Executor, LocalExecutor


@dataclass
class ObservabilityResult:
    """Result of observability stack deployment."""

    stack_path: Path
    started: bool
    error: str | None = None


def deploy_observability(
    data: bytes,
    *,
    data_dir: Path | None = None,
    start: bool = False,
    executor: Executor | None = None,
) -> ObservabilityResult:
    """Deploy the observability stack from a tarball.

    Args:
        data: Raw tarball bytes (anolis-<version>-observability.tar.gz).
        data_dir: Base data directory (default: ~/.anolis).
        start: If True, run `docker compose up -d` after extraction.
        executor: Executor for I/O operations. Defaults to LocalExecutor.

    Returns:
        ObservabilityResult with the stack path and status.
    """
    if executor is None:
        executor = LocalExecutor()
    if data_dir is None:
        data_dir = Path.home() / ".anolis"

    stack_path = data_dir / "observability"
    stack_path.mkdir(parents=True, exist_ok=True)

    # Extract tarball
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tar:
        tar.extractall(path=str(stack_path), filter="data")

    # Create .env from .env.example if it doesn't exist
    env_example = stack_path / ".env.example"
    env_file = stack_path / ".env"
    if env_example.is_file() and not env_file.is_file():
        env_file.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")

    if not start:
        return ObservabilityResult(stack_path=stack_path, started=False)

    # Start the stack
    result = executor.run(
        ["sh", "-c", f"cd {stack_path} && docker compose up -d"],
    )
    if result.returncode != 0:
        return ObservabilityResult(
            stack_path=stack_path,
            started=False,
            error=f"docker compose up failed: {result.stderr.strip()}",
        )

    return ObservabilityResult(stack_path=stack_path, started=True)


def check_docker_available(executor: Executor | None = None) -> tuple[bool, str]:
    """Check if Docker and Docker Compose are available.

    Returns:
        Tuple of (available, detail_message).
    """
    if executor is None:
        executor = LocalExecutor()

    # Check docker
    result = executor.run(["docker", "--version"])
    if result.returncode != 0:
        return False, "Docker is not installed or not in PATH"

    # Check docker compose
    result = executor.run(["docker", "compose", "version"])
    if result.returncode != 0:
        return False, "Docker Compose v2 is not available"

    return True, "Docker and Compose available"
