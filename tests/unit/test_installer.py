"""Unit tests for the local provisioning installer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from anolis_workbench.core import installer

# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------


class TestDetectPlatform:
    def test_aarch64(self) -> None:
        with patch("platform.machine", return_value="aarch64"):
            assert installer.detect_platform() == "linux-arm64"

    def test_arm64_alias(self) -> None:
        with patch("platform.machine", return_value="arm64"):
            assert installer.detect_platform() == "linux-arm64"

    def test_x86_64(self) -> None:
        with patch("platform.machine", return_value="x86_64"):
            assert installer.detect_platform() == "linux-x86_64"

    def test_unsupported_raises(self) -> None:
        with patch("platform.machine", return_value="sparc64"):
            with pytest.raises(installer.PlatformError, match="sparc64"):
                installer.detect_platform()


# ---------------------------------------------------------------------------
# load_compat_matrix
# ---------------------------------------------------------------------------


class TestLoadCompatMatrix:
    def test_from_path(self, tmp_path: Path) -> None:
        matrix_file = tmp_path / "compat.yaml"
        matrix_file.write_text(
            yaml.dump(
                {
                    "workbench_version": "0.3.2",
                    "runtime": {"repo": "anolishq/anolis", "version": "0.1.21"},
                    "providers": {
                        "anolis-provider-bread": {"repo": "anolishq/anolis-provider-bread", "version": "0.2.8"},
                    },
                }
            )
        )
        result = installer.load_compat_matrix(matrix_file)
        assert result["runtime"]["version"] == "0.1.21"

    def test_bundled_loads(self) -> None:
        """Bundled compat matrix should load without error."""
        result = installer.load_compat_matrix()
        assert "runtime" in result
        assert "providers" in result


# ---------------------------------------------------------------------------
# resolve_components
# ---------------------------------------------------------------------------


class TestResolveComponents:
    @pytest.fixture()
    def matrix(self) -> dict:
        return {
            "workbench_version": "0.3.2",
            "runtime": {"repo": "anolishq/anolis", "version": "0.1.21"},
            "providers": {
                "anolis-provider-bread": {"repo": "anolishq/anolis-provider-bread", "version": "0.2.8"},
                "anolis-provider-ezo": {"repo": "anolishq/anolis-provider-ezo", "version": "0.2.5"},
                "anolis-provider-sim": {"repo": "anolishq/anolis-provider-sim", "version": "0.2.3"},
            },
        }

    def test_resolves_runtime_and_providers(self, matrix: dict) -> None:
        components = installer.resolve_components(matrix)
        assert len(components) == 4  # runtime + 3 providers

        runtime = next(c for c in components if c.name == "anolis")
        assert runtime.binary_name == "anolis-runtime"
        assert runtime.version == "0.1.21"
        assert runtime.repo == "anolishq/anolis"

        bread = next(c for c in components if c.name == "anolis-provider-bread")
        assert bread.binary_name == "anolis-provider-bread"
        assert bread.version == "0.2.8"

    def test_skips_incomplete_entries(self) -> None:
        matrix = {
            "runtime": {"repo": "anolishq/anolis"},  # missing version
            "providers": {
                "bread": {},  # missing repo and version
                "ezo": {"repo": "anolishq/ezo", "version": "1.0"},
            },
        }
        components = installer.resolve_components(matrix)
        assert len(components) == 1
        assert components[0].name == "ezo"

    def test_empty_matrix(self) -> None:
        components = installer.resolve_components({})
        assert components == []


# ---------------------------------------------------------------------------
# download_and_verify
# ---------------------------------------------------------------------------


class TestDownloadAndVerify:
    def test_matching_sha256(self) -> None:
        data = b"hello world tarball"
        expected = hashlib.sha256(data).hexdigest()

        mock_resp = MagicMock()
        mock_resp.content = data
        mock_resp.raise_for_status = MagicMock()

        session = MagicMock()
        session.get.return_value = mock_resp

        result = installer.download_and_verify(session, "https://example.com/test.tar.gz", expected)
        assert result == data

    def test_mismatching_sha256_raises(self) -> None:
        data = b"hello world tarball"
        wrong_hash = "0" * 64

        mock_resp = MagicMock()
        mock_resp.content = data
        mock_resp.raise_for_status = MagicMock()

        session = MagicMock()
        session.get.return_value = mock_resp

        with pytest.raises(installer.IntegrityError, match="SHA256 mismatch"):
            installer.download_and_verify(session, "https://example.com/test.tar.gz", wrong_hash)

    def test_network_error_raises(self) -> None:
        import requests

        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("network down")

        with pytest.raises(installer.ManifestError, match="Failed to download"):
            installer.download_and_verify(session, "https://example.com/x.tar.gz", "abc123")


# ---------------------------------------------------------------------------
# provision_project
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


class TestVerifyInstallation:
    def test_success(self, tmp_path: Path) -> None:
        components = [
            installer.ComponentSpec("anolis", "anolishq/anolis", "0.1.21", "anolis-runtime"),
            installer.ComponentSpec("anolis-provider-bread", "anolishq/bread", "0.2.8", "anolis-provider-bread"),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"0.1.21"
        mock_result.stderr = b""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            versions = installer.verify_installation(tmp_path, components)

        assert len(versions) == 2
        assert mock_run.call_count == 2

    def test_binary_not_found(self, tmp_path: Path) -> None:
        components = [
            installer.ComponentSpec("anolis", "anolishq/anolis", "0.1.21", "anolis-runtime"),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 127
        mock_result.stdout = b""
        mock_result.stderr = b"No such file or directory"

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(installer.VerificationError, match="not found"):
                installer.verify_installation(tmp_path, components)


# ---------------------------------------------------------------------------
# fetch_manifest (mocked HTTP)
# ---------------------------------------------------------------------------


class TestFetchManifest:
    def test_success(self) -> None:
        tarball_sha = "a" * 64
        manifest_json = {
            "schema_version": 1,
            "component": "anolis-runtime",
            "version": "0.1.21",
            "platform": "linux-arm64",
            "asset": "anolis-0.1.21-linux-arm64.tar.gz",
            "sha256": tarball_sha,
        }

        release_json = {
            "assets": [
                {
                    "name": "manifest-linux-arm64.json",
                    "browser_download_url": "https://github.com/manifest-dl",
                },
                {
                    "name": "anolis-0.1.21-linux-arm64.tar.gz",
                    "browser_download_url": "https://github.com/tarball-dl",
                },
            ]
        }

        # Mock session
        session = MagicMock()
        release_resp = MagicMock()
        release_resp.json.return_value = release_json
        release_resp.raise_for_status = MagicMock()

        manifest_resp = MagicMock()
        manifest_resp.json.return_value = manifest_json
        manifest_resp.raise_for_status = MagicMock()

        session.get.side_effect = [release_resp, manifest_resp]

        result = installer.fetch_manifest(session, "anolishq/anolis", "0.1.21", "linux-arm64")
        assert result.sha256 == tarball_sha
        assert result.download_url == "https://github.com/tarball-dl"
        assert result.asset_name == "anolis-0.1.21-linux-arm64.tar.gz"

    def test_missing_manifest_asset(self) -> None:
        release_json = {"assets": [{"name": "something-else.zip", "browser_download_url": "x"}]}

        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = release_json
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        with pytest.raises(installer.ManifestError, match="no asset named"):
            installer.fetch_manifest(session, "anolishq/anolis", "0.1.21", "linux-arm64")


# ---------------------------------------------------------------------------
# install_tarball (mocked subprocess)
# ---------------------------------------------------------------------------


class TestInstallTarball:
    def test_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            installer.install_tarball(b"fake-tarball-data", Path("/opt/anolis"))

        mock_run.assert_called_once_with(
            ["sudo", "tar", "-xz", "-C", "/opt/anolis"],
            input=b"fake-tarball-data",
            capture_output=True,
            timeout=30,
        )

    def test_failure_raises(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"permission denied"

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(installer.InstallError, match="permission denied"):
                installer.install_tarball(b"data", Path("/opt/anolis"))


# ---------------------------------------------------------------------------
# check_existing_binaries
# ---------------------------------------------------------------------------


class TestCheckExistingBinaries:
    def test_no_binaries_found(self, tmp_path: Path) -> None:
        components = [
            installer.ComponentSpec("anolis", "repo", "0.1.21", "anolis-runtime"),
        ]
        result = installer.check_existing_binaries(tmp_path, components)
        assert result == {"anolis-runtime": None}

    def test_binary_exists(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "anolis-runtime").write_text("#!/bin/sh\necho 0.1.21")
        (bin_dir / "anolis-runtime").chmod(0o755)

        components = [
            installer.ComponentSpec("anolis", "repo", "0.1.21", "anolis-runtime"),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"0.1.21"

        with patch("subprocess.run", return_value=mock_result):
            result = installer.check_existing_binaries(tmp_path, components)
        assert result["anolis-runtime"] == "0.1.21"


# ---------------------------------------------------------------------------
# get_system_provider_names
# ---------------------------------------------------------------------------


class TestGetSystemProviderNames:
    def test_extracts_provider_names(self, tmp_path: Path) -> None:
        system = {
            "schema_version": 1,
            "meta": {"name": "test", "created": ""},
            "paths": {
                "runtime_executable": "build/anolis-runtime",
                "providers": {
                    "bread0": {"executable": "build/anolis-provider-bread", "bus_path": "/dev/i2c-1"},
                    "ezo0": {"executable": "build/anolis-provider-ezo", "bus_path": "/dev/i2c-2"},
                },
            },
            "topology": {},
        }
        system_file = tmp_path / "system.json"
        system_file.write_text(json.dumps(system), encoding="utf-8")

        result = installer.get_system_provider_names(system_file)
        assert result == {"anolis-provider-bread", "anolis-provider-ezo"}

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        result = installer.get_system_provider_names(tmp_path / "missing.json")
        assert result is None

    def test_no_providers_returns_empty_set(self, tmp_path: Path) -> None:
        system = {
            "schema_version": 1,
            "meta": {"name": "test", "created": ""},
            "paths": {"runtime_executable": "build/anolis-runtime", "providers": {}},
            "topology": {},
        }
        system_file = tmp_path / "system.json"
        system_file.write_text(json.dumps(system), encoding="utf-8")

        result = installer.get_system_provider_names(system_file)
        assert result == set()
