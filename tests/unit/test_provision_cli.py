"""Unit tests for provision CLI validation helpers."""

from __future__ import annotations

import argparse

from anolis_workbench.cli.provision_cli import _validate_system_template


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
