"""Unit tests for anolis_workbench.core.bundler."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from anolis_workbench.core.bundler import build_bundle
from anolis_workbench.core.installer import ComponentSpec


@pytest.fixture()
def components() -> list[ComponentSpec]:
    return [
        ComponentSpec(name="anolis", version="0.1.21", repo="cambrilian/anolis", binary_name="anolis-runtime"),
        ComponentSpec(
            name="bread", version="0.2.8", repo="cambrilian/anolis-provider-bread", binary_name="anolis-provider-bread"
        ),
    ]


@pytest.fixture()
def tarballs(components: list[ComponentSpec]) -> list[tuple[ComponentSpec, bytes]]:
    return [(components[0], b"fake-runtime-tarball"), (components[1], b"fake-bread-tarball")]


@pytest.fixture()
def fake_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a minimal template for rendering."""
    templates_dir = tmp_path / "templates"
    tpl_dir = templates_dir / "bioreactor-manual"
    tpl_dir.mkdir(parents=True)

    system = {
        "schema_version": 1,
        "meta": {"name": "template", "created": ""},
        "paths": {
            "runtime_executable": "build/anolis-runtime",
            "providers": {
                "bread0": {"executable": "build/anolis-provider-bread", "bus_path": "/dev/i2c-1"},
            },
        },
        "topology": {
            "runtime": {
                "grpc_port": 50051,
                "telemetry": {"enabled": True, "push_interval_ms": 1000, "endpoint": "http://localhost:4317"},
            },
            "providers": {
                "bread0": {
                    "type": "bread",
                    "devices": [{"address": "0x20", "label": "motor_a", "profile": "stepper_bipolar"}],
                },
            },
        },
    }
    (tpl_dir / "system.json").write_text(json.dumps(system), encoding="utf-8")
    monkeypatch.setattr("anolis_workbench.core.bundler.paths_module.TEMPLATES_ROOT", templates_dir)
    return templates_dir


class TestBuildBundle:
    def test_creates_expected_structure(
        self, tmp_path: Path, components: list[ComponentSpec], tarballs: list, fake_template: Path
    ) -> None:
        out_dir = tmp_path / "bundle-out"

        with patch(
            "anolis_workbench.core.bundler.renderer_module.render",
            return_value={"anolis-runtime.yaml": "runtime: true\n", "providers/bread0.yaml": "bread: true\n"},
        ):
            result = build_bundle(
                components=components,
                tarballs=tarballs,
                template_name="bioreactor-manual",
                project_name="bioreactor-v1",
                platform_str="linux-arm64",
                out_dir=out_dir,
            )

        assert result.bundle_path == out_dir
        assert result.platform == "linux-arm64"

        # Check directory structure
        assert (out_dir / "binaries").is_dir()
        assert (out_dir / "project").is_dir()
        assert (out_dir / "project" / "providers").is_dir()
        assert (out_dir / "checksums.sha256").is_file()
        assert (out_dir / "manifest.json").is_file()
        assert (out_dir / "install.sh").is_file()

    def test_tarballs_written_correctly(
        self, tmp_path: Path, components: list[ComponentSpec], tarballs: list, fake_template: Path
    ) -> None:
        out_dir = tmp_path / "bundle-out"

        with patch("anolis_workbench.core.bundler.renderer_module.render", return_value={}):
            build_bundle(
                components=components,
                tarballs=tarballs,
                template_name="bioreactor-manual",
                project_name="bioreactor-v1",
                platform_str="linux-arm64",
                out_dir=out_dir,
            )

        rt_tarball = out_dir / "binaries" / "anolis-0.1.21-linux-arm64.tar.gz"
        bread_tarball = out_dir / "binaries" / "bread-0.2.8-linux-arm64.tar.gz"
        assert rt_tarball.read_bytes() == b"fake-runtime-tarball"
        assert bread_tarball.read_bytes() == b"fake-bread-tarball"

    def test_checksums_correct(
        self, tmp_path: Path, components: list[ComponentSpec], tarballs: list, fake_template: Path
    ) -> None:
        out_dir = tmp_path / "bundle-out"

        with patch("anolis_workbench.core.bundler.renderer_module.render", return_value={}):
            build_bundle(
                components=components,
                tarballs=tarballs,
                template_name="bioreactor-manual",
                project_name="bioreactor-v1",
                platform_str="linux-arm64",
                out_dir=out_dir,
            )

        checksums = (out_dir / "checksums.sha256").read_text(encoding="utf-8")
        rt_sha = hashlib.sha256(b"fake-runtime-tarball").hexdigest()
        bread_sha = hashlib.sha256(b"fake-bread-tarball").hexdigest()
        assert f"{rt_sha}  binaries/anolis-0.1.21-linux-arm64.tar.gz" in checksums
        assert f"{bread_sha}  binaries/bread-0.2.8-linux-arm64.tar.gz" in checksums

    def test_manifest_json(
        self, tmp_path: Path, components: list[ComponentSpec], tarballs: list, fake_template: Path
    ) -> None:
        out_dir = tmp_path / "bundle-out"

        with patch("anolis_workbench.core.bundler.renderer_module.render", return_value={}):
            build_bundle(
                components=components,
                tarballs=tarballs,
                template_name="bioreactor-manual",
                project_name="bioreactor-v1",
                platform_str="linux-arm64",
                out_dir=out_dir,
                workbench_version="0.4.0",
            )

        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == 1
        assert manifest["bundle_format"] == "anolis-bundle-v1"
        assert manifest["project"] == "bioreactor-v1"
        assert manifest["target_platform"] == "linux-arm64"
        assert manifest["install_prefix"] == "/usr/local"
        assert manifest["workbench_version"] == "0.4.0"
        assert len(manifest["components"]) == 2
        assert manifest["components"][0]["name"] == "anolis"

    def test_install_sh_executable(
        self, tmp_path: Path, components: list[ComponentSpec], tarballs: list, fake_template: Path
    ) -> None:
        out_dir = tmp_path / "bundle-out"

        with patch("anolis_workbench.core.bundler.renderer_module.render", return_value={}):
            build_bundle(
                components=components,
                tarballs=tarballs,
                template_name="bioreactor-manual",
                project_name="bioreactor-v1",
                platform_str="linux-arm64",
                out_dir=out_dir,
            )

        install_sh = out_dir / "install.sh"
        mode = install_sh.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH

        content = install_sh.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env bash")
        assert "sha256sum -c checksums.sha256" in content
        assert "bioreactor-v1" in content

    def test_project_configs_rendered(
        self, tmp_path: Path, components: list[ComponentSpec], tarballs: list, fake_template: Path
    ) -> None:
        out_dir = tmp_path / "bundle-out"

        with patch(
            "anolis_workbench.core.bundler.renderer_module.render",
            return_value={"anolis-runtime.yaml": "port: 50051\n", "providers/bread0.yaml": "type: bread\n"},
        ):
            build_bundle(
                components=components,
                tarballs=tarballs,
                template_name="bioreactor-manual",
                project_name="bioreactor-v1",
                platform_str="linux-arm64",
                out_dir=out_dir,
            )

        assert (out_dir / "project" / "system.json").is_file()
        assert (out_dir / "project" / "anolis-runtime.yaml").is_file()
        assert (out_dir / "project" / "providers" / "bread0.yaml").is_file()

        system = json.loads((out_dir / "project" / "system.json").read_text(encoding="utf-8"))
        assert system["meta"]["name"] == "bioreactor-v1"
        assert system["paths"]["runtime_executable"] == "/usr/local/bin/anolis-runtime"
        assert system["paths"]["providers"]["bread0"]["executable"] == "/usr/local/bin/anolis-provider-bread"

    def test_custom_install_prefix(
        self, tmp_path: Path, components: list[ComponentSpec], tarballs: list, fake_template: Path
    ) -> None:
        out_dir = tmp_path / "bundle-out"

        with patch("anolis_workbench.core.bundler.renderer_module.render", return_value={}):
            result = build_bundle(
                components=components,
                tarballs=tarballs,
                template_name="bioreactor-manual",
                project_name="bioreactor-v1",
                platform_str="linux-arm64",
                out_dir=out_dir,
                install_prefix=Path("/opt/anolis"),
            )

        assert result.install_prefix == "/opt/anolis"
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["install_prefix"] == "/opt/anolis"

    def test_existing_output_dir_raises(
        self, tmp_path: Path, components: list[ComponentSpec], tarballs: list, fake_template: Path
    ) -> None:
        out_dir = tmp_path / "bundle-out"
        out_dir.mkdir()

        with pytest.raises(ValueError, match="already exists"):
            build_bundle(
                components=components,
                tarballs=tarballs,
                template_name="bioreactor-manual",
                project_name="bioreactor-v1",
                platform_str="linux-arm64",
                out_dir=out_dir,
            )

    def test_missing_template_raises(
        self, tmp_path: Path, components: list[ComponentSpec], tarballs: list, fake_template: Path
    ) -> None:
        out_dir = tmp_path / "bundle-out"

        with pytest.raises(FileNotFoundError, match="not found"):
            build_bundle(
                components=components,
                tarballs=tarballs,
                template_name="nonexistent-template",
                project_name="bioreactor-v1",
                platform_str="linux-arm64",
                out_dir=out_dir,
            )
