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
    """`--template` now defaults to None, so "was it given?" is a real question
    rather than a comparison against whatever the default happens to be."""

    def test_both_system_and_template_returns_false(self) -> None:
        args = argparse.Namespace(system="/tmp/rig", template="custom-template")
        assert _validate_system_template(args) is False

    def test_system_only_returns_true(self) -> None:
        args = argparse.Namespace(system="/tmp/rig", template=None)
        assert _validate_system_template(args) is True

    def test_template_only_returns_true(self) -> None:
        args = argparse.Namespace(system=None, template="custom-template")
        assert _validate_system_template(args) is True

    def test_neither_returns_true(self) -> None:
        args = argparse.Namespace(system=None, template=None)
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


def test_every_workspace_subcommand_can_be_provisioned(tmp_path, monkeypatch) -> None:
    """`bundle` has no --force (bundling must never replace a project), so
    _provision_workspace has to tolerate its absence. Drive the REAL parser —
    a hand-built Namespace hides exactly this class of bug, and did."""
    from anolis_workbench.cli import provision_cli
    from anolis_workbench.core import paths as paths_module

    systems_root = tmp_path / "systems"
    systems_root.mkdir()
    monkeypatch.setattr(paths_module, "SYSTEMS_ROOT", systems_root)
    monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

    for argv in (
        ["install", "--project", "p1", "--template", "sim-quickstart"],
        ["bundle", "--project", "p2", "--template", "sim-quickstart", "--arch", "x86_64", "--out", str(tmp_path / "o")],
    ):
        monkeypatch.setattr("sys.argv", ["anolis-provision", *argv])
        args = provision_cli._parse_args()
        project_dir = provision_cli._provision_workspace(args)
        assert (project_dir / "machine-profile.yaml").is_file(), argv

    # Second call on an existing project reuses it rather than re-seeding.
    monkeypatch.setattr(
        "sys.argv", ["anolis-provision", "bundle", "--project", "p2", "--arch", "x86_64", "--out", str(tmp_path / "o")]
    )
    assert provision_cli._provision_workspace(provision_cli._parse_args()) == systems_root / "p2"


class TestNameDefaults:
    """A default project called `bioreactor-v1` seeded from a simulator template
    describes a machine that does not exist. The project name now follows the
    template, so `anolis-provision install` with no flags is self-describing."""

    def test_project_defaults_to_the_template_name(self) -> None:
        from anolis_workbench.cli.provision_cli import _resolve_names

        assert _resolve_names(argparse.Namespace(project=None, template=None)) == (
            "sim-quickstart",
            "sim-quickstart",
        )
        assert _resolve_names(argparse.Namespace(project=None, template="my-tpl")) == ("my-tpl", "my-tpl")
        assert _resolve_names(argparse.Namespace(project="rig-a", template=None)) == ("rig-a", "sim-quickstart")


def test_missing_template_error_points_at_import(tmp_path, monkeypatch) -> None:
    """A fleet file or runbook naming a removed template must fail with a
    message that says what to do instead, not a bare not-found."""
    from anolis_workbench.core import installer

    systems_root = tmp_path / "systems"
    systems_root.mkdir()
    with pytest.raises(FileNotFoundError, match="import the machine's own"):
        installer.provision_project("bioreactor-manual", "p", tmp_path / "prefix", systems_root=systems_root)
