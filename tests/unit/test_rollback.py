"""Unit tests for the rollback module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from anolis_workbench.core.rollback import (
    backup_binaries,
    backup_binary,
    rollback,
    rollback_binary,
)

# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


class TestBackupBinary:
    def test_backs_up_existing_binary(self) -> None:
        mock_executor = MagicMock()
        mock_executor.file_exists.return_value = True
        mock_executor.run.return_value = MagicMock(returncode=0)

        result = backup_binary("anolis-runtime", Path("/opt/anolis"), executor=mock_executor)

        assert result is True
        mock_executor.file_exists.assert_called_with("/opt/anolis/bin/anolis-runtime")
        mock_executor.run.assert_called_once()
        cmd = mock_executor.run.call_args[0][0]
        assert cmd == ["cp", "/opt/anolis/bin/anolis-runtime", "/opt/anolis/bin/anolis-runtime.prev"]

    def test_no_backup_if_binary_missing(self) -> None:
        mock_executor = MagicMock()
        mock_executor.file_exists.return_value = False

        result = backup_binary("anolis-runtime", Path("/opt/anolis"), executor=mock_executor)

        assert result is False
        mock_executor.run.assert_not_called()

    def test_backup_binaries_multiple(self) -> None:
        mock_executor = MagicMock()
        mock_executor.file_exists.return_value = True
        mock_executor.run.return_value = MagicMock(returncode=0)

        result = backup_binaries(
            ["anolis-runtime", "anolis-provider-bread"],
            Path("/opt/anolis"),
            executor=mock_executor,
        )

        assert result == ["anolis-runtime", "anolis-provider-bread"]
        assert mock_executor.run.call_count == 2


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class TestRollbackBinary:
    def test_rollback_existing_prev(self) -> None:
        mock_executor = MagicMock()
        mock_executor.file_exists.return_value = True
        mock_executor.run.return_value = MagicMock(returncode=0)

        result = rollback_binary("anolis-runtime", Path("/opt/anolis"), executor=mock_executor)

        assert result is True
        cmd = mock_executor.run.call_args[0][0]
        assert cmd == ["mv", "/opt/anolis/bin/anolis-runtime.prev", "/opt/anolis/bin/anolis-runtime"]

    def test_rollback_no_prev_returns_false(self) -> None:
        mock_executor = MagicMock()
        mock_executor.file_exists.return_value = False

        result = rollback_binary("anolis-runtime", Path("/opt/anolis"), executor=mock_executor)

        assert result is False


class TestRollback:
    def test_rolls_back_all_binaries(self) -> None:
        mock_executor = MagicMock()
        mock_executor.file_exists.return_value = True
        mock_executor.run.return_value = MagicMock(returncode=0)

        result = rollback(
            ["anolis-runtime", "anolis-provider-bread"],
            Path("/opt/anolis"),
            executor=mock_executor,
        )

        assert result.rolled_back == ["anolis-runtime", "anolis-provider-bread"]
        assert result.failed == []
        assert result.error is None

    def test_reports_missing_prev(self) -> None:
        mock_executor = MagicMock()
        mock_executor.file_exists.return_value = False

        result = rollback(
            ["anolis-runtime"],
            Path("/opt/anolis"),
            executor=mock_executor,
        )

        assert result.rolled_back == []
        assert result.failed == ["anolis-runtime"]
        assert result.error is not None
        assert "No .prev" in result.error

    def test_restarts_service_on_systemd(self) -> None:
        mock_executor = MagicMock()
        mock_executor.file_exists.return_value = True
        mock_executor.run.return_value = MagicMock(returncode=0)

        result = rollback(
            ["anolis-runtime"],
            Path("/opt/anolis"),
            project_name="bioreactor-v1",
            systemd=True,
            executor=mock_executor,
        )

        assert result.service_restarted is True
        # Last call should be systemctl restart
        last_call = mock_executor.run.call_args_list[-1]
        assert "systemctl" in last_call[0][0]
        assert "restart" in last_call[0][0]
        assert "anolis-bioreactor-v1.service" in last_call[0][0]

    def test_partial_rollback(self) -> None:
        mock_executor = MagicMock()
        # First binary has .prev, second doesn't
        mock_executor.file_exists.side_effect = [True, True, False]
        mock_executor.run.return_value = MagicMock(returncode=0)

        result = rollback(
            ["anolis-runtime", "anolis-provider-bread"],
            Path("/opt/anolis"),
            executor=mock_executor,
        )

        assert "anolis-runtime" in result.rolled_back
        assert "anolis-provider-bread" in result.failed
