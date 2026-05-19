"""Telemetry export configuration for Anolis provisioning.

Generates the telemetry-export config file and optional systemd unit
for the anolis-telemetry-export service.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from anolis_workbench.core.executor import Executor, LocalExecutor

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

_DEFAULT_TELEMETRY_CONFIG = {
    "runtime": {
        "grpc_endpoint": "127.0.0.1:50051",
    },
    "influxdb": {
        "url": "http://127.0.0.1:8086",
        "org": "anolis",
        "bucket": "bioreactor",
        "token": "${INFLUXDB_TOKEN}",
    },
}

_SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=Anolis Telemetry Export — {project_name}
After=anolis-{project_name}.service

[Service]
Type=simple
User={user}
Environment=INFLUXDB_TOKEN=
ExecStart=anolis-telemetry-export --config {config_path}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class TelemetryConfigResult:
    """Result of telemetry export configuration."""

    config_path: Path
    service_installed: bool
    service_name: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_telemetry_config(
    project_name: str,
    *,
    systems_root: Path | None = None,
    grpc_endpoint: str = "127.0.0.1:50051",
    influxdb_url: str = "http://127.0.0.1:8086",
    influxdb_bucket: str | None = None,
) -> Path:
    """Render the telemetry-export config file into the project directory.

    Args:
        project_name: Project name (e.g. "bioreactor-v1").
        systems_root: Root for project directories (default: ~/.anolis/systems).
        grpc_endpoint: Runtime gRPC endpoint.
        influxdb_url: InfluxDB URL.
        influxdb_bucket: InfluxDB bucket name (defaults to project_name without version).

    Returns:
        Path to the rendered config file.
    """
    if systems_root is None:
        systems_root = Path.home() / ".anolis" / "systems"

    project_dir = systems_root / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    config = dict(_DEFAULT_TELEMETRY_CONFIG)
    config["runtime"] = {"grpc_endpoint": grpc_endpoint}

    # Derive bucket name from project (strip version suffix)
    bucket = influxdb_bucket or project_name.rsplit("-", 1)[0]
    config["influxdb"] = {
        "url": influxdb_url,
        "org": "anolis",
        "bucket": bucket,
        "token": "${INFLUXDB_TOKEN}",
    }

    config_path = project_dir / "telemetry-export.yaml"
    config_path.write_text(
        "# Anolis Telemetry Export configuration\n"
        "# Set INFLUXDB_TOKEN environment variable before starting the service.\n"
        + yaml.dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    return config_path


def install_telemetry_service(
    project_name: str,
    config_path: Path,
    *,
    user: str | None = None,
    executor: Executor | None = None,
) -> TelemetryConfigResult:
    """Install a systemd unit for the telemetry-export service.

    Args:
        project_name: Project name for service naming.
        config_path: Path to the telemetry-export config file.
        user: User to run the service as (default: current user).
        executor: Executor for commands.

    Returns:
        TelemetryConfigResult with status.
    """
    if executor is None:
        executor = LocalExecutor()
    if user is None:
        import os

        user = os.environ.get("USER", "root")

    service_name = f"anolis-telemetry-export-{project_name}"
    unit_content = _SYSTEMD_UNIT_TEMPLATE.format(
        project_name=project_name,
        user=user,
        config_path=str(config_path),
    )

    unit_path = f"/etc/systemd/system/{service_name}.service"

    # Write unit file
    result = executor.run(
        ["tee", unit_path],
        input=unit_content.encode(),
        sudo=True,
    )
    if result.returncode != 0:
        return TelemetryConfigResult(
            config_path=config_path,
            service_installed=False,
            error=f"Failed to write unit file: {result.stderr.strip()}",
        )

    # Reload + enable + start
    for cmd in [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", f"{service_name}.service"],
        ["systemctl", "start", f"{service_name}.service"],
    ]:
        result = executor.run(cmd, sudo=True)
        if result.returncode != 0:
            return TelemetryConfigResult(
                config_path=config_path,
                service_installed=False,
                service_name=service_name,
                error=f"Failed: {' '.join(cmd)}: {result.stderr.strip()}",
            )

    return TelemetryConfigResult(
        config_path=config_path,
        service_installed=True,
        service_name=service_name,
    )


def install_telemetry_export_package(
    version: str,
    *,
    executor: Executor | None = None,
    offline_wheels_dir: Path | None = None,
) -> bool:
    """Install the anolis-telemetry-export package.

    Args:
        version: Package version to install.
        executor: Executor for pip commands.
        offline_wheels_dir: If set, install from local wheels (offline mode).

    Returns:
        True if install succeeded.
    """
    if executor is None:
        executor = LocalExecutor()

    if offline_wheels_dir is not None:
        cmd = [
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(offline_wheels_dir),
            f"anolis-telemetry-export=={version}",
        ]
    else:
        cmd = ["pip", "install", f"anolis-telemetry-export=={version}"]

    result = executor.run(cmd)
    return result.returncode == 0
