"""Unit tests for the telemetry_config module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from anolis_workbench.core.telemetry_config import (
    install_telemetry_export_package,
    install_telemetry_service,
    render_telemetry_config,
)


class TestRenderTelemetryConfig:
    def test_creates_config_file(self, tmp_path: Path) -> None:
        config_path = render_telemetry_config("bioreactor-v1", systems_root=tmp_path)

        assert config_path == tmp_path / "bioreactor-v1" / "telemetry-export.yaml"
        assert config_path.is_file()

    def test_config_content(self, tmp_path: Path) -> None:
        config_path = render_telemetry_config("bioreactor-v1", systems_root=tmp_path)

        content = config_path.read_text(encoding="utf-8")
        assert "INFLUXDB_TOKEN" in content
        assert "grpc_endpoint" in content

        # Parse YAML (skip comment lines)
        data = yaml.safe_load(content)
        assert data["runtime"]["grpc_endpoint"] == "127.0.0.1:50051"
        assert data["influxdb"]["url"] == "http://127.0.0.1:8086"
        assert data["influxdb"]["bucket"] == "bioreactor"

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


class TestInstallTelemetryService:
    def test_installs_systemd_unit(self) -> None:
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=0, stderr="")

        result = install_telemetry_service(
            "bioreactor-v1",
            Path("/home/pi/.anolis/systems/bioreactor-v1/telemetry-export.yaml"),
            user="pi",
            executor=mock_executor,
        )

        assert result.service_installed is True
        assert result.service_name == "anolis-telemetry-export-bioreactor-v1"
        # Should call tee + daemon-reload + enable + start = 4 calls
        assert mock_executor.run.call_count == 4

    def test_failure_returns_error(self) -> None:
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=1, stderr="permission denied")

        result = install_telemetry_service(
            "bioreactor-v1",
            Path("/tmp/config.yaml"),
            user="pi",
            executor=mock_executor,
        )

        assert result.service_installed is False
        assert result.error is not None


class TestInstallTelemetryExportPackage:
    def test_online_install(self) -> None:
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=0)

        success = install_telemetry_export_package("0.1.0", executor=mock_executor)
        assert success is True

        cmd = mock_executor.run.call_args[0][0]
        assert "pip" in cmd
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
        assert "--no-index" in cmd
        assert "--find-links" in cmd
        assert "/bundle/wheels" in cmd

    def test_failure_returns_false(self) -> None:
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=1)

        success = install_telemetry_export_package("0.1.0", executor=mock_executor)
        assert success is False
