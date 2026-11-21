"""Tests for package-context validation and replay checks."""

from __future__ import annotations

import hashlib
import pathlib
import zipfile
from typing import Callable

import pytest
import yaml

from anolis_workbench.core import canonical, exporter, package_validator


@pytest.fixture()
def make_project(canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path):
    """A canonical project whose exported package carries a redacted token."""

    def _make(_tmp_path: pathlib.Path, name: str) -> pathlib.Path:
        pdir = canonical_project(tmp_path / name, machine_id=name)
        variant = pdir / canonical.variant_relpath(canonical.MANUAL_VARIANT)
        doc = yaml.safe_load(variant.read_text(encoding="utf-8"))
        doc["telemetry"] = {
            "enabled": True,
            "influxdb": {
                "url": "http://localhost:8086",
                "org": "anolis",
                "bucket": "anolis",
                "token": "fixture-secret",
            },
        }
        variant.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return pdir

    return _make


def test_validate_package_accepts_valid_archive(make_project, tmp_path: pathlib.Path) -> None:
    project_dir = make_project(tmp_path, "valid-package")
    package_path = tmp_path / "valid-package.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=package_path)
    package_validator.validate_package(package_path)


def test_validate_package_rejects_checksum_drift(make_project, tmp_path: pathlib.Path) -> None:
    project_dir = make_project(tmp_path, "checksum-drift")
    package_path = tmp_path / "checksum-drift.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=package_path)

    extract_dir = tmp_path / "extract"
    with zipfile.ZipFile(package_path, mode="r") as archive:
        archive.extractall(extract_dir)

    runtime_path = extract_dir / "runtime" / "anolis-runtime.yaml"
    runtime_path.write_text(runtime_path.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

    with pytest.raises(package_validator.PackageValidationError, match="Checksum mismatch"):
        package_validator.validate_package(extract_dir)


def test_validate_package_rejects_secret_leak_even_with_updated_checksums(make_project, tmp_path: pathlib.Path) -> None:
    project_dir = make_project(tmp_path, "secret-drift")
    package_path = tmp_path / "secret-drift.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=package_path)

    extract_dir = tmp_path / "extract-secret"
    with zipfile.ZipFile(package_path, mode="r") as archive:
        archive.extractall(extract_dir)

    runtime_path = extract_dir / "runtime" / "anolis-runtime.yaml"
    payload = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    payload.setdefault("telemetry", {}).setdefault("influxdb", {})["token"] = "reintroduced-secret"
    runtime_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    _recompute_checksums(extract_dir)

    with pytest.raises(package_validator.PackageValidationError, match="Secret-like token value leaked"):
        package_validator.validate_package(extract_dir)


def test_validate_package_rejects_provider_path_escape(make_project, tmp_path: pathlib.Path) -> None:
    project_dir = make_project(tmp_path, "escape-drift")
    package_path = tmp_path / "escape-drift.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=package_path)

    extract_dir = tmp_path / "extract-escape"
    with zipfile.ZipFile(package_path, mode="r") as archive:
        archive.extractall(extract_dir)

    runtime_path = extract_dir / "runtime" / "anolis-runtime.yaml"
    payload = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    payload["providers"][0]["args"] = ["--config", "../../outside.yaml"]
    runtime_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    _recompute_checksums(extract_dir)

    with pytest.raises(package_validator.PackageValidationError, match="path escapes package root"):
        package_validator.validate_package(extract_dir)


def _recompute_checksums(package_root: pathlib.Path) -> None:
    lines: list[str] = []
    for candidate in sorted(package_root.rglob("*")):
        if not candidate.is_file():
            continue
        rel = candidate.relative_to(package_root).as_posix()
        if rel == "meta/checksums.sha256":
            continue
        lines.append(f"{hashlib.sha256(candidate.read_bytes()).hexdigest()}  {rel}")
    (package_root / "meta" / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# An unreadable package is "Validation failed", not a traceback (#674)
# ---------------------------------------------------------------------------


def _run_validate_cli(monkeypatch: pytest.MonkeyPatch, package: pathlib.Path) -> int:
    from anolis_workbench.cli import validate_cli

    monkeypatch.setattr("sys.argv", ["anolis-validate", str(package)])
    return validate_cli.main()


@pytest.mark.parametrize(
    ("rel", "content"),
    [
        ("machine-profile.yaml", b"key: [unclosed"),
        ("machine-profile.yaml", b"key: \xff\xfe"),
        ("meta/provenance.json", b"{not json"),
        ("runtime/anolis-runtime.yaml", b"providers: [unclosed"),
    ],
)
def test_an_unreadable_package_file_fails_validation_naming_the_file(
    make_project,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rel: str,
    content: bytes,
) -> None:
    project_dir = make_project(tmp_path, "unreadable")
    package_path = tmp_path / "unreadable.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=package_path)
    extract_dir = tmp_path / "extract-unreadable"
    with zipfile.ZipFile(package_path, mode="r") as archive:
        archive.extractall(extract_dir)
    (extract_dir / rel).write_bytes(content)
    _recompute_checksums(extract_dir)

    with pytest.raises(package_validator.PackageValidationError, match=rel):
        package_validator.validate_package(extract_dir)
    assert _run_validate_cli(monkeypatch, extract_dir) == 1
    err = capsys.readouterr().err
    assert err.startswith("Validation failed: ") and rel in err


def test_a_checksum_file_that_is_not_utf8_fails_validation(make_project, tmp_path: pathlib.Path) -> None:
    project_dir = make_project(tmp_path, "checksums-utf8")
    package_path = tmp_path / "checksums-utf8.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=package_path)
    extract_dir = tmp_path / "extract-checksums"
    with zipfile.ZipFile(package_path, mode="r") as archive:
        archive.extractall(extract_dir)
    (extract_dir / "meta" / "checksums.sha256").write_bytes(b"\xff\xfe")

    with pytest.raises(package_validator.PackageValidationError, match="meta/checksums.sha256"):
        package_validator.validate_package(extract_dir)


def test_an_archive_that_is_not_a_zip_fails_validation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    corrupt = tmp_path / "corrupt.anpkg"
    corrupt.write_bytes(b"not a zip at all")

    with pytest.raises(package_validator.PackageValidationError, match="corrupt.anpkg"):
        package_validator.validate_package(corrupt)
    assert _run_validate_cli(monkeypatch, corrupt) == 1
    assert capsys.readouterr().err.startswith("Validation failed: ")
