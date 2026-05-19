"""Unit tests for the observability module."""

from __future__ import annotations

import tarfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anolis_workbench.core.observability import (
    check_docker_available,
    deploy_observability,
)


def _make_obs_tarball(include_env_example: bool = True) -> bytes:
    """Create a minimal observability tarball in memory."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # docker-compose.yml
        compose = b"version: '3'\nservices:\n  influxdb:\n    image: influxdb\n"
        info = tarfile.TarInfo(name="docker-compose.yml")
        info.size = len(compose)
        tar.addfile(info, BytesIO(compose))

        if include_env_example:
            env = b"INFLUXDB_TOKEN=changeme\n"
            info = tarfile.TarInfo(name=".env.example")
            info.size = len(env)
            tar.addfile(info, BytesIO(env))

    return buf.getvalue()


class TestDeployObservability:
    def test_extracts_tarball(self, tmp_path: Path) -> None:
        data = _make_obs_tarball()
        result = deploy_observability(data, data_dir=tmp_path)

        assert result.stack_path == tmp_path / "observability"
        assert (tmp_path / "observability" / "docker-compose.yml").is_file()
        assert result.started is False
        assert result.error is None

    def test_creates_env_from_example(self, tmp_path: Path) -> None:
        data = _make_obs_tarball(include_env_example=True)
        deploy_observability(data, data_dir=tmp_path)

        env_file = tmp_path / "observability" / ".env"
        assert env_file.is_file()
        assert "INFLUXDB_TOKEN" in env_file.read_text(encoding="utf-8")

    def test_does_not_overwrite_existing_env(self, tmp_path: Path) -> None:
        obs_dir = tmp_path / "observability"
        obs_dir.mkdir(parents=True)
        env_file = obs_dir / ".env"
        env_file.write_text("MY_CUSTOM=value\n", encoding="utf-8")

        data = _make_obs_tarball()
        deploy_observability(data, data_dir=tmp_path)

        assert env_file.read_text(encoding="utf-8") == "MY_CUSTOM=value\n"

    def test_start_runs_docker_compose(self, tmp_path: Path) -> None:
        data = _make_obs_tarball()
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=0, stderr="")

        result = deploy_observability(data, data_dir=tmp_path, start=True, executor=mock_executor)

        assert result.started is True
        mock_executor.run.assert_called_once()
        call_args = mock_executor.run.call_args[0][0]
        assert "docker compose up -d" in " ".join(call_args)

    def test_start_failure_reports_error(self, tmp_path: Path) -> None:
        data = _make_obs_tarball()
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=1, stderr="daemon not running")

        result = deploy_observability(data, data_dir=tmp_path, start=True, executor=mock_executor)

        assert result.started is False
        assert result.error is not None
        assert "daemon not running" in result.error

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="../evil.sh")
            info.size = 5
            tar.addfile(info, BytesIO(b"evil\n"))
        data = buf.getvalue()

        with pytest.raises(ValueError, match="Unsafe path"):
            deploy_observability(data, data_dir=tmp_path)


class TestCheckDockerAvailable:
    def test_docker_available(self) -> None:
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=0)

        available, detail = check_docker_available(mock_executor)
        assert available is True

    def test_docker_not_installed(self) -> None:
        mock_executor = MagicMock()
        mock_executor.run.return_value = MagicMock(returncode=1)

        available, detail = check_docker_available(mock_executor)
        assert available is False
        assert "Docker" in detail
