"""Unit tests for provision CLI validation helpers."""

from __future__ import annotations

import argparse
import sys

import pytest

from anolis_workbench.cli.provision_cli import (
    _parse_args,
    _validate_system_template,
    _wants_observability,
    _wants_telemetry_export,
)


class TestValidateSystemTemplate:
    def test_both_system_and_template_returns_false(self) -> None:
        args = argparse.Namespace(system="/tmp/sys.json", template="custom-template")
        assert _validate_system_template(args) is False

    def test_system_only_returns_true(self) -> None:
        args = argparse.Namespace(system="/tmp/sys.json", template="bioreactor-manual")
        assert _validate_system_template(args) is True

    def test_template_only_returns_true(self) -> None:
        args = argparse.Namespace(system=None, template="custom-template")
        assert _validate_system_template(args) is True

    def test_neither_returns_true(self) -> None:
        args = argparse.Namespace(system=None, template="bioreactor-manual")
        assert _validate_system_template(args) is True


class TestWantsHelpers:
    # Namespaces intentionally omit any `profile` attr — its presence would mean
    # the helper still reads the removed taxonomy (would raise AttributeError).
    def test_wants_observability_when_flag_set(self) -> None:
        args = argparse.Namespace(with_observability=True, with_telemetry_export=False)
        assert _wants_observability(args) is True

    def test_no_observability_by_default(self) -> None:
        args = argparse.Namespace(with_observability=False, with_telemetry_export=False)
        assert _wants_observability(args) is False

    def test_wants_telemetry_export_when_flag_set(self) -> None:
        args = argparse.Namespace(with_observability=False, with_telemetry_export=True)
        assert _wants_telemetry_export(args) is True

    def test_no_telemetry_export_by_default(self) -> None:
        args = argparse.Namespace(with_observability=False, with_telemetry_export=False)
        assert _wants_telemetry_export(args) is False

    def test_flags_are_independent(self) -> None:
        args = argparse.Namespace(with_observability=True, with_telemetry_export=False)
        assert _wants_observability(args) is True
        assert _wants_telemetry_export(args) is False


class TestProfileFlagRemoved:
    # The collapsed --profile taxonomy was removed (#235); argparse must reject
    # it with exit code 2 on every subcommand that used to define it. Each argv
    # supplies that subcommand's required args so --profile is the only error.
    @pytest.mark.parametrize(
        "argv",
        [
            ["anolis-provision", "install", "--profile", "manual"],
            ["anolis-provision", "remote", "--target", "pi@host", "--profile", "manual"],
        ],
    )
    def test_profile_flag_rejected(self, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exc:
            _parse_args()
        assert exc.value.code == 2
