"""Unit tests for the local provisioning installer."""

from __future__ import annotations

from pathlib import Path

import pytest

from anolis_workbench.core import canonical, installer, machine_profile

# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------


class TestProvisionProject:
    def test_creates_project_with_patched_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A provisioned project is the canonical artifact set, re-keyed onto
        its own machine_id, with ONLY the sidecar's host paths pointed at the
        install prefix — the configs keep the deploy tokens install.sh rewrites."""
        systems_root = tmp_path / "systems"
        systems_root.mkdir()
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
        monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

        prefix = tmp_path / "prefix"
        prefix.mkdir()

        project_dir = installer.provision_project(
            template_name="sim-quickstart",
            project_name="test-project",
            install_prefix=prefix,
        )

        assert project_dir == systems_root / "test-project"
        document = canonical.read_project(project_dir)

        # Host binary paths (the dev-launch residual) point at the prefix...
        host = document["host_paths"]
        assert host["runtime_executable"] == str(prefix / "bin" / "anolis-runtime")
        assert host["providers"]["sim0"]["executable"] == str(prefix / "bin" / "anolis-provider-sim")

        # ...and the canonical configs do NOT: they stay prefix-agnostic.
        manual = document["variants"][canonical.MANUAL_VARIANT]
        for entry in manual["providers"]:
            assert str(prefix) not in entry["command"]
            assert canonical.command_kind(entry["command"]) == "sim"

        # Re-keyed onto the new project, or it would deploy into the template's dir.
        assert document["profile"]["machine_id"] == "test-project"
        assert all(
            "/projects/test-project/" in arg for entry in manual["providers"] for arg in entry["args"] if "../" in arg
        )
        assert document["meta"]["name"] == "test-project"

        # Provider-owned config must survive untouched.
        assert document["providers"]["sim0"]["config"]["provider"]["name"] == "sim0"

        assert (project_dir / canonical.provider_config_relpath("sim", "sim0")).is_file()

    def test_raises_on_existing_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        systems_root = tmp_path / "systems"
        systems_root.mkdir()
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
        monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

        # Create a fake existing project
        (systems_root / "existing-project").mkdir()

        with pytest.raises(ValueError, match="already exists"):
            installer.provision_project("sim-quickstart", "existing-project", Path("/opt/anolis"))

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

        project_dir = installer.provision_project("sim-quickstart", "force-test", prefix, force=True)
        assert (project_dir / machine_profile.PROFILE_FILENAME).exists()
        assert not (project_dir / "old-file.txt").exists()


# ---------------------------------------------------------------------------
# verify_installation (mocked subprocess)
# ---------------------------------------------------------------------------
