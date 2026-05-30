"""Tests for anolis_workbench.core.preflight."""

from __future__ import annotations

from unittest.mock import MagicMock

from anolis_workbench.core.executor import RunResult
from anolis_workbench.core.preflight import (
    CheckResult,
    PreflightResult,
    _check_architecture,
    _check_disk_space,
    _check_i2c_enabled,
    _check_i2c_permissions,
    _check_python,
    _check_sudo,
    format_preflight_result,
    run_preflight,
)

# ---------------------------------------------------------------------------
# CheckResult / PreflightResult dataclass tests
# ---------------------------------------------------------------------------


class TestPreflightResult:
    def test_all_passed(self):
        result = PreflightResult(
            checks=[
                CheckResult(name="A", passed=True, detail="ok", fatal=True),
                CheckResult(name="B", passed=True, detail="ok", fatal=False),
            ]
        )
        assert result.passed is True
        assert result.has_warnings is False

    def test_fatal_failure(self):
        result = PreflightResult(
            checks=[
                CheckResult(name="A", passed=False, detail="bad", fatal=True),
                CheckResult(name="B", passed=True, detail="ok", fatal=False),
            ]
        )
        assert result.passed is False

    def test_non_fatal_warning(self):
        result = PreflightResult(
            checks=[
                CheckResult(name="A", passed=True, detail="ok", fatal=True),
                CheckResult(name="B", passed=False, detail="warn", fatal=False),
            ]
        )
        assert result.passed is True
        assert result.has_warnings is True


# ---------------------------------------------------------------------------
# Individual check tests
# ---------------------------------------------------------------------------


class TestCheckArchitecture:
    def test_aarch64(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout="aarch64\n", stderr="")
        result = _check_architecture(executor)
        assert result.passed is True
        assert result.detail == "aarch64"

    def test_x86_64(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout="x86_64\n", stderr="")
        result = _check_architecture(executor)
        assert result.passed is True

    def test_unsupported(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout="armv7l\n", stderr="")
        result = _check_architecture(executor)
        assert result.passed is False
        assert result.fatal is True

    def test_command_failure(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=1, stdout="", stderr="error")
        result = _check_architecture(executor)
        assert result.passed is False
        assert result.fatal is True


class TestCheckI2C:
    def test_i2c_exists(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout="/dev/i2c-1\n", stderr="")
        result = _check_i2c_enabled(executor)
        assert result.passed is True

    def test_i2c_missing(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=2, stdout="", stderr="No such file")
        result = _check_i2c_enabled(executor)
        assert result.passed is False
        assert result.fatal is True
        assert result.fix_hint is not None


class TestCheckI2CPermissions:
    def test_user_in_group(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout="pi : pi adm i2c gpio\n", stderr="")
        result = _check_i2c_permissions(executor)
        assert result.passed is True

    def test_user_not_in_group(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout="pi : pi adm gpio\n", stderr="")
        result = _check_i2c_permissions(executor)
        assert result.passed is False
        assert result.fatal is False


class TestCheckDiskSpace:
    def test_sufficient_space(self):
        executor = MagicMock()
        # 2 GB in bytes
        executor.run.return_value = RunResult(returncode=0, stdout="Avail\n2147483648\n", stderr="")
        result = _check_disk_space(executor, "/opt/anolis")
        assert result.passed is True
        assert "GB" in result.detail

    def test_insufficient_space(self):
        executor = MagicMock()
        # 10 MB in bytes
        executor.run.return_value = RunResult(returncode=0, stdout="Avail\n10485760\n", stderr="")
        result = _check_disk_space(executor, "/opt/anolis")
        assert result.passed is False
        assert result.fatal is True

    def test_df_failure(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=1, stdout="", stderr="error")
        result = _check_disk_space(executor, "/opt/anolis")
        assert result.passed is False


class TestCheckPython:
    def test_python_311(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout="Python 3.11.2\n", stderr="")
        result = _check_python(executor)
        assert result.passed is True
        assert "3.11.2" in result.detail

    def test_python_39(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout="Python 3.9.2\n", stderr="")
        result = _check_python(executor)
        assert result.passed is False
        assert result.fatal is False

    def test_python_not_found(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=127, stdout="", stderr="not found")
        result = _check_python(executor)
        assert result.passed is False


class TestCheckSudo:
    def test_nopasswd_configured(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=0, stdout="", stderr="")
        result = _check_sudo(executor)
        assert result.passed is True

    def test_password_required(self):
        executor = MagicMock()
        executor.run.return_value = RunResult(returncode=1, stdout="", stderr="a password is required")
        result = _check_sudo(executor)
        assert result.passed is False
        assert result.fatal is False
        assert result.fix_hint is not None
        assert "sudoers" in result.fix_hint


# ---------------------------------------------------------------------------
# Integration: run_preflight
# ---------------------------------------------------------------------------


class TestRunPreflight:
    def test_all_pass(self):
        executor = MagicMock()
        executor.run.side_effect = [
            RunResult(returncode=0, stdout="aarch64\n", stderr=""),  # arch
            RunResult(returncode=0, stdout="/dev/i2c-1\n", stderr=""),  # i2c
            RunResult(returncode=0, stdout="pi : pi i2c\n", stderr=""),  # groups
            RunResult(returncode=0, stdout="Avail\n2147483648\n", stderr=""),  # disk
            RunResult(returncode=0, stdout="Python 3.11.2\n", stderr=""),  # python
            RunResult(returncode=0, stdout="", stderr=""),  # sudo
        ]
        result = run_preflight(executor)
        assert result.passed is True
        assert len(result.checks) == 6

    def test_fatal_failure_aborts(self):
        executor = MagicMock()
        executor.run.side_effect = [
            RunResult(returncode=0, stdout="armv7l\n", stderr=""),  # arch fails
            RunResult(returncode=0, stdout="/dev/i2c-1\n", stderr=""),
            RunResult(returncode=0, stdout="pi : pi i2c\n", stderr=""),
            RunResult(returncode=0, stdout="Avail\n2147483648\n", stderr=""),
            RunResult(returncode=0, stdout="Python 3.11.2\n", stderr=""),
            RunResult(returncode=0, stdout="", stderr=""),
        ]
        result = run_preflight(executor)
        assert result.passed is False


# ---------------------------------------------------------------------------
# Format output
# ---------------------------------------------------------------------------


class TestFormatPreflight:
    def test_output_format(self):
        result = PreflightResult(
            checks=[
                CheckResult(name="Architecture", passed=True, detail="aarch64", fatal=True),
                CheckResult(
                    name="Sudo",
                    passed=False,
                    detail="password required",
                    fatal=False,
                    fix_hint="Add NOPASSWD entry",
                ),
            ]
        )
        output = format_preflight_result(result, target="pi@192.168.1.10")
        assert "pi@192.168.1.10" in output
        assert "✓ Architecture: aarch64" in output
        assert "✗ Sudo: password required" in output
        assert "Add NOPASSWD entry" in output
