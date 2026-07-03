"""Unit tests for the telemetry_config module.

The rendered-config assertions mirror the anolis-telemetry-export service's
own load_config contract (telemetry_export/export_core/config.py): required
`server:` + `influxdb:` + `limits:` sections, secrets from env only.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from anolis_workbench.core.telemetry_config import (
    install_telemetry_export_package,
    render_telemetry_config,
)


class TestRenderTelemetryConfig:
    def test_creates_config_file(self, tmp_path: Path) -> None:
        config_path = render_telemetry_config("bioreactor-v1", systems_root=tmp_path)

        assert config_path == tmp_path / "bioreactor-v1" / "telemetry-export.yaml"
        assert config_path.is_file()

    def test_config_satisfies_service_contract(self, tmp_path: Path) -> None:
        """Mirror of the service's load_config requiredness (server/influxdb/limits)."""
        config_path = render_telemetry_config("bioreactor-v1", systems_root=tmp_path)
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        # Required sections must be mappings.
        for section in ("server", "influxdb", "limits"):
            assert isinstance(data.get(section), dict), f"{section}: section is required"

        # server: host/port shape the service parses.
        assert data["server"]["host"] == "127.0.0.1"
        assert data["server"]["port"] == 8091

        # influxdb: url/org/bucket are required non-empty strings.
        for key in ("url", "org", "bucket"):
            assert isinstance(data["influxdb"][key], str) and data["influxdb"][key]
        assert data["influxdb"]["url"] == "http://127.0.0.1:8086"
        assert data["influxdb"]["bucket"] == "bioreactor"

        # limits: integers >= 1 (the section is required by the service).
        assert all(isinstance(v, int) and v >= 1 for v in data["limits"].values())
        assert data["limits"]["max_span_seconds"] == 86400

    def test_no_secrets_in_rendered_file(self, tmp_path: Path) -> None:
        """Tokens come from ANOLIS_EXPORT_* env vars, never the file."""
        config_path = render_telemetry_config("bioreactor-v1", systems_root=tmp_path)
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        assert "token" not in data["influxdb"]
        assert "auth_token" not in data["server"]
        # The comment header names the real env vars.
        content = config_path.read_text(encoding="utf-8")
        assert "ANOLIS_EXPORT_AUTH_TOKEN" in content
        assert "ANOLIS_EXPORT_INFLUX_TOKEN" in content
        # The old broken placeholder must not come back.
        assert "${INFLUXDB_TOKEN}" not in content

    def test_no_unread_sections(self, tmp_path: Path) -> None:
        """The service never reads `runtime:` — don't render it."""
        config_path = render_telemetry_config("bioreactor-v1", systems_root=tmp_path)
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert "runtime" not in data

    def test_custom_bucket(self, tmp_path: Path) -> None:
        config_path = render_telemetry_config(
            "bioreactor-v1",
            systems_root=tmp_path,
            influxdb_bucket="custom-bucket",
        )

        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert data["influxdb"]["bucket"] == "custom-bucket"

    def test_derives_bucket_from_project_name(self, tmp_path: Path) -> None:
        config_path = render_telemetry_config("fermentor-v2", systems_root=tmp_path)

        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert data["influxdb"]["bucket"] == "fermentor"


class TestInstallTelemetryExportPackage:
    def test_online_install(self) -> None:
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=0)

        success = install_telemetry_export_package("0.1.0", executor=mock_executor)
        assert success is True

        cmd = mock_executor.run.call_args[0][0]
        # pip3, not pip — "pip" is not in the executor allowlist, so the old
        # spelling raised ValueError before the install could ever run.
        assert cmd[0] == "pip3"
        assert "anolis-telemetry-export==0.1.0" in cmd

    def test_offline_install(self) -> None:
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=0)

        success = install_telemetry_export_package(
            "0.1.0",
            executor=mock_executor,
            offline_wheels_dir=Path("/bundle/wheels"),
        )
        assert success is True

        cmd = mock_executor.run.call_args[0][0]
        assert cmd[0] == "pip3"
        assert "--no-index" in cmd
        assert "--find-links" in cmd
        assert "/bundle/wheels" in cmd

    def test_failure_returns_false(self) -> None:
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=1)

        success = install_telemetry_export_package("0.1.0", executor=mock_executor)
        assert success is False
