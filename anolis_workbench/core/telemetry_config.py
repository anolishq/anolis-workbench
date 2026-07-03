"""Telemetry export configuration for Anolis provisioning.

Renders the config file for the anolis-telemetry-export service, matching
the service's own load_config contract (telemetry_export/export_core/
config.py in anolishq/anolis-telemetry-export): required `server:`,
`influxdb:`, and `limits:` sections; secrets come from the
ANOLIS_EXPORT_AUTH_TOKEN / ANOLIS_EXPORT_INFLUX_TOKEN env vars, never the
rendered file. Service/unit installation is not handled here — folding
telemetry provisioning into install.sh is anolishq/anolis#137.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from anolis_workbench.core.executor import Executor, LocalExecutor

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

# Reference limits from the service's example config
# (anolis-telemetry-export/config/bioreactor/telemetry-export.bioreactor.yaml).
# The section is required by load_config; these are the documented defaults.
_DEFAULT_LIMITS = {
    "max_span_seconds": 86400,
    "max_rows": 50000,
    "max_response_bytes": 10_000_000,
    "max_stream_bytes": 10_000_000,
    "max_selector_items": 128,
    "request_timeout_seconds": 15,
    "max_request_bytes": 200_000,
    "max_manifest_entries": 10_000,
    "manifest_ttl_seconds": 86400,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_telemetry_config(
    project_name: str,
    *,
    systems_root: Path | None = None,
    server_host: str = "127.0.0.1",
    server_port: int = 8091,
    influxdb_url: str = "http://127.0.0.1:8086",
    influxdb_bucket: str | None = None,
) -> Path:
    """Render the telemetry-export config file into the project directory.

    The rendered file satisfies the service's load_config contract
    (`server:` + `influxdb:` + `limits:`). Tokens are intentionally not
    written — the service resolves ANOLIS_EXPORT_AUTH_TOKEN and
    ANOLIS_EXPORT_INFLUX_TOKEN from the environment.

    Args:
        project_name: Project name (e.g. "bioreactor-v1").
        systems_root: Root for project directories (default: ~/.anolis/systems).
        server_host: Export service bind host.
        server_port: Export service port (service default: 8091).
        influxdb_url: InfluxDB URL.
        influxdb_bucket: InfluxDB bucket name (defaults to project_name without version).

    Returns:
        Path to the rendered config file.
    """
    if systems_root is None:
        systems_root = Path.home() / ".anolis" / "systems"

    project_dir = systems_root / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    # Derive bucket name from project (strip version suffix)
    bucket = influxdb_bucket or project_name.rsplit("-", 1)[0]
    config = {
        "server": {
            "host": server_host,
            "port": server_port,
        },
        "influxdb": {
            "url": influxdb_url,
            "org": "anolis",
            "bucket": bucket,
        },
        "limits": dict(_DEFAULT_LIMITS),
    }

    config_path = project_dir / "telemetry-export.yaml"
    config_path.write_text(
        "# Anolis Telemetry Export configuration\n"
        "# Secrets are read from the environment, not this file:\n"
        "#   ANOLIS_EXPORT_AUTH_TOKEN   - export API auth token (required)\n"
        "#   ANOLIS_EXPORT_INFLUX_TOKEN - InfluxDB token (required)\n"
        + yaml.dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    return config_path


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
            "pip3",
            "install",
            "--no-index",
            "--find-links",
            str(offline_wheels_dir),
            f"anolis-telemetry-export=={version}",
        ]
    else:
        cmd = ["pip3", "install", f"anolis-telemetry-export=={version}"]

    result = executor.run(cmd)
    return result.returncode == 0
