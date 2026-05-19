"""Unit tests for provision CLI validation helpers."""

from __future__ import annotations

import argparse

from anolis_workbench.cli.provision_cli import (
    _validate_system_template,
    _wants_observability,
    _wants_telemetry_export,
)
from anolis_workbench.core.installer import VALID_PROFILES, profile_includes


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


class TestProfiles:
    def test_valid_profiles_list(self) -> None:
        assert "manual" in VALID_PROFILES
        assert "telemetry" in VALID_PROFILES
        assert "automation" in VALID_PROFILES
        assert "full" in VALID_PROFILES

    def test_manual_profile_has_no_extras(self) -> None:
        assert not profile_includes("manual", "observability")
        assert not profile_includes("manual", "telemetry_export")

    def test_telemetry_profile_includes_both(self) -> None:
        assert profile_includes("telemetry", "observability")
        assert profile_includes("telemetry", "telemetry_export")

    def test_full_profile_includes_both(self) -> None:
        assert profile_includes("full", "observability")
        assert profile_includes("full", "telemetry_export")

    def test_automation_profile_no_telemetry(self) -> None:
        assert not profile_includes("automation", "observability")
        assert not profile_includes("automation", "telemetry_export")

    def test_unknown_profile_returns_false(self) -> None:
        assert not profile_includes("nonexistent", "observability")


class TestWantsHelpers:
    def test_wants_observability_from_profile(self) -> None:
        args = argparse.Namespace(profile="telemetry", with_observability=False, with_telemetry_export=False)
        assert _wants_observability(args) is True

    def test_wants_observability_from_flag(self) -> None:
        args = argparse.Namespace(profile="manual", with_observability=True, with_telemetry_export=False)
        assert _wants_observability(args) is True

    def test_no_observability_manual(self) -> None:
        args = argparse.Namespace(profile="manual", with_observability=False, with_telemetry_export=False)
        assert _wants_observability(args) is False

    def test_wants_telemetry_from_profile(self) -> None:
        args = argparse.Namespace(profile="full", with_observability=False, with_telemetry_export=False)
        assert _wants_telemetry_export(args) is True

    def test_wants_telemetry_from_flag(self) -> None:
        args = argparse.Namespace(profile="manual", with_observability=False, with_telemetry_export=True)
        assert _wants_telemetry_export(args) is True
