"""Unit tests for the local provisioning installer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from anolis_workbench.core import installer

# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------


class TestProvisionProject:
    def test_creates_project_with_patched_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """provision_project should patch paths and produce valid project files."""
        # Set up systems root in tmp
        systems_root = tmp_path / "systems"
        systems_root.mkdir()
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
        monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

        prefix = tmp_path / "prefix"
        prefix.mkdir()

        project_dir = installer.provision_project(
            template_name="bioreactor-manual",
            project_name="test-project",
            install_prefix=prefix,
        )

        assert project_dir == systems_root / "test-project"
        assert project_dir.exists()

        # Check system.json has patched paths
        system = json.loads((project_dir / "system.json").read_text())
        assert system["paths"]["runtime_executable"] == str(prefix / "bin" / "anolis-runtime")
        assert system["paths"]["providers"]["bread0"]["executable"] == str(prefix / "bin" / "anolis-provider-bread")
        assert system["paths"]["providers"]["ezo0"]["executable"] == str(prefix / "bin" / "anolis-provider-ezo")
        # bus_path should remain unchanged
        assert system["paths"]["providers"]["bread0"]["bus_path"] == "/dev/i2c-1"

        # Check meta was updated
        assert system["meta"]["name"] == "test-project"

        # Check rendered configs exist
        assert (project_dir / "anolis-runtime.yaml").exists()
        assert (project_dir / "providers" / "bread0.yaml").exists()
        assert (project_dir / "providers" / "ezo0.yaml").exists()

        # Check anolis-runtime.yaml has correct command paths
        runtime_yaml = yaml.safe_load((project_dir / "anolis-runtime.yaml").read_text())
        provider_entries = runtime_yaml.get("providers", [])
        for entry in provider_entries:
            assert str(prefix / "bin") in entry["command"]

    def test_raises_on_existing_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        systems_root = tmp_path / "systems"
        systems_root.mkdir()
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
        monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

        # Create a fake existing project
        (systems_root / "existing-project").mkdir()

        with pytest.raises(ValueError, match="already exists"):
            installer.provision_project("bioreactor-manual", "existing-project", Path("/opt/anolis"))

    def test_force_overwrites(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        systems_root = tmp_path / "systems"
        systems_root.mkdir()
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
        monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

        prefix = tmp_path / "prefix"
        prefix.mkdir()

        # Create existing project directory
        (systems_root / "force-test").mkdir()
        (systems_root / "force-test" / "old-file.txt").write_text("old")

        project_dir = installer.provision_project("bioreactor-manual", "force-test", prefix, force=True)
        assert (project_dir / "system.json").exists()


# ---------------------------------------------------------------------------
# verify_installation (mocked subprocess)
# ---------------------------------------------------------------------------
