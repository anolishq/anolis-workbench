"""Tests for anolis_workbench.core.systemd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from anolis_workbench.core.executor import RunResult
from anolis_workbench.core.systemd import (
    install_service,
    render_unit_file,
    service_name,
    wait_ready,
)

# ---------------------------------------------------------------------------
# service_name
# ---------------------------------------------------------------------------


class TestServiceName:
    def test_basic(self):
        assert service_name("bioreactor-v1") == "anolis-bioreactor-v1.service"

    def test_simple(self):
        assert service_name("test") == "anolis-test.service"


# ---------------------------------------------------------------------------
# render_unit_file
# ---------------------------------------------------------------------------


class TestRenderUnitFile:
    def test_renders_all_fields(self):
        content = render_unit_file(
            "bioreactor-v1",
            install_prefix=Path("/usr/local"),
            systems_root=Path("/home/pi/.anolis/systems"),
            user="pi",
        )
        assert "Description=Anolis Runtime — bioreactor-v1" in content
        assert "User=pi" in content
        assert "WorkingDirectory=/home/pi/.anolis/systems/bioreactor-v1" in content
        assert "/usr/local/bin/anolis-runtime --config" in content
        assert "anolis-runtime.yaml" in content
        assert "WantedBy=multi-user.target" in content

    def test_custom_prefix(self):
        content = render_unit_file(
            "test-project",
            install_prefix=Path("/opt/anolis"),
            systems_root=Path("/data/systems"),
            user="anolis",
        )
        assert "/opt/anolis/bin/anolis-runtime" in content
        assert "WorkingDirectory=/data/systems/test-project" in content
        assert "User=anolis" in content


# ---------------------------------------------------------------------------
# install_service
# ---------------------------------------------------------------------------


class TestInstallService:
    def test_successful_install(self):
        executor = MagicMock()
        executor.file_exists.return_value = False  # service doesn't exist yet
        executor.run.return_value = RunResult(returncode=0, stdout="", stderr="")

        result = install_service(
            "bioreactor-v1",
            executor=executor,
            install_prefix=Path("/usr/local"),
            systems_root=Path("/home/pi/.anolis/systems"),
            user="pi",
        )

        assert result.installed is True
        assert result.enabled is True
        assert result.started is True
        assert result.error is None
        assert result.service_name == "anolis-bioreactor-v1.service"

        # Verify write_file was called for the temp unit file
        executor.write_file.assert_called_once()
        tmp_path = executor.write_file.call_args[0][0]
        assert "anolis-bioreactor-v1.service" in tmp_path

        # Verify systemctl start (not restart) was called since service is new
        run_calls = executor.run.call_args_list
        start_call = [c for c in run_calls if "start" in str(c)]
        assert any("start" in str(c) and "restart" not in str(c) for c in start_call)

    def test_restart_existing_service(self):
        executor = MagicMock()
        executor.file_exists.return_value = True  # service already exists
        executor.run.return_value = RunResult(returncode=0, stdout="", stderr="")

        result = install_service(
            "bioreactor-v1",
            executor=executor,
            install_prefix=Path("/usr/local"),
            systems_root=Path("/home/pi/.anolis/systems"),
            user="pi",
        )

        assert result.started is True
        # Verify restart was used instead of start
        run_calls = executor.run.call_args_list
        assert any("restart" in str(c) for c in run_calls)

    def test_mv_failure(self):
        executor = MagicMock()
        executor.file_exists.return_value = False
        executor.run.return_value = RunResult(returncode=1, stdout="", stderr="Permission denied")

        result = install_service(
            "bioreactor-v1",
            executor=executor,
            install_prefix=Path("/usr/local"),
            systems_root=Path("/home/pi/.anolis/systems"),
            user="pi",
        )

        assert result.installed is False
        assert result.error is not None
        assert "Permission denied" in result.error

    def test_daemon_reload_failure(self):
        executor = MagicMock()
        executor.file_exists.return_value = False

        # mv succeeds, daemon-reload fails
        def run_side_effect(cmd, *, sudo=False):
            if "daemon-reload" in cmd:
                return RunResult(returncode=1, stdout="", stderr="reload error")
            return RunResult(returncode=0, stdout="", stderr="")

        executor.run.side_effect = run_side_effect

        result = install_service(
            "bioreactor-v1",
            executor=executor,
            install_prefix=Path("/usr/local"),
            systems_root=Path("/home/pi/.anolis/systems"),
            user="pi",
        )

        assert result.installed is True
        assert result.enabled is False
        assert "reload error" in result.error

    def test_enable_failure(self):
        executor = MagicMock()
        executor.file_exists.return_value = False

        def run_side_effect(cmd, *, sudo=False):
            if "enable" in cmd:
                return RunResult(returncode=1, stdout="", stderr="enable error")
            return RunResult(returncode=0, stdout="", stderr="")

        executor.run.side_effect = run_side_effect

        result = install_service(
            "bioreactor-v1",
            executor=executor,
            install_prefix=Path("/usr/local"),
            systems_root=Path("/home/pi/.anolis/systems"),
            user="pi",
        )

        assert result.installed is True
        assert result.enabled is False
        assert result.started is False

    def test_start_failure(self):
        executor = MagicMock()
        executor.file_exists.return_value = False

        def run_side_effect(cmd, *, sudo=False):
            if "start" in cmd:
                return RunResult(returncode=1, stdout="", stderr="start error")
            return RunResult(returncode=0, stdout="", stderr="")

        executor.run.side_effect = run_side_effect

        result = install_service(
            "bioreactor-v1",
            executor=executor,
            install_prefix=Path("/usr/local"),
            systems_root=Path("/home/pi/.anolis/systems"),
            user="pi",
        )

        assert result.installed is True
        assert result.enabled is True
        assert result.started is False
        assert "start error" in result.error


# ---------------------------------------------------------------------------
# wait_ready
# ---------------------------------------------------------------------------


class TestWaitReady:
    @patch("anolis_workbench.core.systemd.time")
    def test_ready_immediately(self, mock_time):
        mock_time.monotonic.side_effect = [0.0, 1.0]
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout='{"status":"ok"}', stderr="")

        result = wait_ready(executor, timeout_seconds=30)
        assert result is True

    @patch("anolis_workbench.core.systemd.time")
    def test_ready_after_retries(self, mock_time):
        # First call: monotonic for deadline (0), then loop: 1, 3, 5
        mock_time.monotonic.side_effect = [0.0, 1.0, 3.0, 5.0]
        executor = MagicMock()
        executor.run.side_effect = [
            RunResult(returncode=7, stdout="", stderr="connection refused"),
            RunResult(returncode=7, stdout="", stderr="connection refused"),
            RunResult(returncode=0, stdout='{"status":"ok"}', stderr=""),
        ]

        result = wait_ready(executor, timeout_seconds=30)
        assert result is True

    @patch("anolis_workbench.core.systemd.time")
    def test_timeout(self, mock_time):
        # deadline at 30, but all checks past deadline
        mock_time.monotonic.side_effect = [0.0, 31.0]
        executor = MagicMock()

        result = wait_ready(executor, timeout_seconds=30)
        assert result is False
