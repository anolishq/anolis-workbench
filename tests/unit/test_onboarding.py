"""Unit tests for onboarding detection endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from anolis_workbench.server.routes import onboarding


class FakeHandler:
    """Minimal mock of BaseHTTPRequestHandler."""

    def __init__(self) -> None:
        self._responses: list[tuple[int, dict[str, Any]]] = []

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self._responses.append((status, data))


class TestGetOnboardingStatus:
    def test_first_run_when_nothing_exists(self, tmp_path: Path) -> None:
        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=tmp_path / "missing"),
            patch.object(onboarding, "_has_runtime", return_value=False),
        ):
            onboarding.get_onboarding_status(handler)

        status, data = handler._responses[0]
        assert status == 200
        assert data["first_run"] is True
        assert data["has_projects"] is False
        assert data["has_runtime"] is False

    def test_not_first_run_when_projects_exist(self, tmp_path: Path) -> None:
        systems = tmp_path / "systems"
        systems.mkdir()
        (systems / "bioreactor-v1").mkdir()

        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=systems),
            patch.object(onboarding, "_has_runtime", return_value=False),
        ):
            onboarding.get_onboarding_status(handler)

        status, data = handler._responses[0]
        assert status == 200
        assert data["first_run"] is False
        assert data["has_projects"] is True

    def test_not_first_run_when_runtime_exists(self, tmp_path: Path) -> None:
        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=tmp_path / "missing"),
            patch.object(onboarding, "_has_runtime", return_value=True),
        ):
            onboarding.get_onboarding_status(handler)

        status, data = handler._responses[0]
        assert status == 200
        assert data["first_run"] is False
        assert data["has_runtime"] is True

    def test_runtime_path_included(self) -> None:
        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=Path("/nonexistent")),
            patch.object(onboarding, "_has_runtime", return_value=False),
            patch.object(onboarding, "_runtime_path", return_value="/usr/local/bin/anolis-runtime"),
        ):
            onboarding.get_onboarding_status(handler)

        _, data = handler._responses[0]
        assert data["runtime_path"] == "/usr/local/bin/anolis-runtime"


class TestHelpers:
    def test_has_projects_empty_dir(self, tmp_path: Path) -> None:
        with patch.object(onboarding, "_systems_root", return_value=tmp_path):
            assert onboarding._has_projects() is False

    def test_has_projects_with_subdirs(self, tmp_path: Path) -> None:
        (tmp_path / "project1").mkdir()
        with patch.object(onboarding, "_systems_root", return_value=tmp_path):
            assert onboarding._has_projects() is True

    def test_has_projects_nonexistent_dir(self) -> None:
        with patch.object(onboarding, "_systems_root", return_value=Path("/no/such/path")):
            assert onboarding._has_projects() is False

    def test_has_runtime_false(self) -> None:
        with patch.object(onboarding, "_runtime_path", return_value="/no/such/binary"):
            assert onboarding._has_runtime() is False

    def test_has_runtime_true(self, tmp_path: Path) -> None:
        binary = tmp_path / "anolis-runtime"
        binary.touch()
        with patch.object(onboarding, "_runtime_path", return_value=str(binary)):
            assert onboarding._has_runtime() is True
