"""Unit tests for deterministic handoff package export core."""

from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import stat
import zipfile
from typing import Callable

import pytest
import requests
import yaml

from anolis_workbench.core import canonical, exporter, releases


@pytest.fixture(autouse=True)
def _stub_release_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed the release cache and block network so tests never hit GitHub."""
    monkeypatch.setattr(
        releases,
        "_RELEASE_CACHE",
        {"anolishq/anolis": "0.1.26", "anolishq/anolis-provider-sim": "0.2.1"},
    )

    def _no_network(*args: object, **kwargs: object) -> None:
        raise requests.RequestException("network disabled in tests")

    monkeypatch.setattr(releases.requests, "get", _no_network)


EXPORT_CREATED = "2026-04-16T19:01:02.999999+00:00"


@pytest.fixture()
def project_dir(canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path) -> pathlib.Path:
    """A canonical project carrying an automation variant and a telemetry token.

    Since #255 the exporter sources from DISK rather than re-rendering, so the
    fixture is the real thing: whatever these files say is what ships.
    """
    pdir = canonical_project(
        tmp_path / "export-deterministic",
        machine_id="export-deterministic",
        behavior="behaviors/local.xml",
        created=EXPORT_CREATED,
    )
    _patch_variant(
        pdir,
        canonical.MANUAL_VARIANT,
        telemetry={
            "enabled": True,
            "influxdb": {
                "url": "http://localhost:8086",
                "org": "anolis",
                "bucket": "anolis",
                "token": "super-secret",
            },
        },
    )
    return pdir


def _patch_variant(project_dir: pathlib.Path, variant: str, **updates: object) -> dict:
    path = project_dir / canonical.variant_relpath(variant)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc.update(updates)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return doc  # type: ignore[no-any-return]


def test_build_package_is_deterministic_and_rewrites_runtime_paths(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    out_a = tmp_path / "a.anpkg"
    out_b = tmp_path / "b.anpkg"

    exporter.build_package(project_dir=project_dir, out_path=out_a)
    exporter.build_package(project_dir=project_dir, out_path=out_b)

    data_a = out_a.read_bytes()
    assert data_a == out_b.read_bytes()

    with zipfile.ZipFile(io.BytesIO(data_a), mode="r") as archive:
        assert sorted(archive.namelist()) == sorted(
            [
                "machine-profile.yaml",
                "meta/checksums.sha256",
                "meta/provenance.json",
                "providers/sim0.yaml",
                "runtime/anolis-runtime.yaml",
                # Declared by the profile, though the manual variant does not
                # reference it: every declared behavior is carried (#675).
                "runtime/behaviors/local.xml",
            ]
        )

        runtime_payload = yaml.safe_load(archive.read("runtime/anolis-runtime.yaml"))
        assert runtime_payload["providers"][0]["args"] == ["--config", "providers/sim0.yaml"]
        assert "token" not in runtime_payload.get("telemetry", {}).get("influxdb", {})
        assert "influx_token" not in runtime_payload.get("telemetry", {})

        profile = yaml.safe_load(archive.read("machine-profile.yaml"))
        assert profile["runtime_profiles"] == {"manual": "runtime/anolis-runtime.yaml"}
        assert profile["providers"]["sim0"]["config"] == "providers/sim0.yaml"
        assert profile["behaviors"] == ["runtime/behaviors/local.xml"]

        provenance = json.loads(archive.read("meta/provenance.json").decode("utf-8"))
        assert provenance["exported_at"] == "2026-04-16T19:01:02Z"
        assert provenance["package_format_version"] == 1
        assert provenance["source_project"] == "export-deterministic"


def test_export_carries_the_projects_authored_pins(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """The pins in the package are the project's own. The old exporter resolved
    them from live GitHub lookups, so two exports of the same unchanged project
    could disagree — and exporting offline produced a package with no
    components block at all."""
    out = tmp_path / "out.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=out)
    with zipfile.ZipFile(out) as archive:
        profile = yaml.safe_load(archive.read("machine-profile.yaml"))
    assert profile["components"]["runtime"] == {"repo": "anolishq/anolis", "version": "0.1.27"}
    assert profile["components"]["providers"]["sim"] == {
        "repo": "anolishq/anolis-provider-sim",
        "version": "0.2.5",
    }


def test_export_records_the_variants_it_could_not_carry(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """Package format v1 holds ONE runtime config, so a project with an
    automation variant exports only the inert manual one. The recipient must
    be able to tell that from the package instead of assuming it is whole."""
    out = tmp_path / "out.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=out)
    with zipfile.ZipFile(out) as archive:
        provenance = json.loads(archive.read("meta/provenance.json").decode("utf-8"))
    assert provenance["exported_variant"] == "manual"
    assert provenance["omitted_variants"] == ["automation"]


def test_export_carries_a_behavior_tree_the_exported_variant_references(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """An imported profile's `manual` variant may legitimately reference a
    behavior tree (#226 only warns about inertness), and it has to be carried
    and re-pointed at the package layout."""
    _patch_variant(
        project_dir,
        canonical.MANUAL_VARIANT,
        automation={"enabled": True, "behavior_tree": "behaviors/local.xml"},
    )
    out = tmp_path / "out.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=out)

    with zipfile.ZipFile(out) as archive:
        assert "runtime/behaviors/local.xml" in archive.namelist()
        runtime_payload = yaml.safe_load(archive.read("runtime/anolis-runtime.yaml"))
        assert runtime_payload["automation"]["behavior_tree"] == "runtime/behaviors/local.xml"
        profile = yaml.safe_load(archive.read("machine-profile.yaml"))
        assert profile["behaviors"] == ["runtime/behaviors/local.xml"]


def _declare_behaviors(project_dir: pathlib.Path, files: dict[str, bytes]) -> None:
    profile_path = project_dir / "machine-profile.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    for rel, content in files.items():
        (project_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / rel).write_bytes(content)
    profile["behaviors"] = list(files)
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def test_every_declared_behavior_is_carried_not_only_the_referenced_one(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The contract rewrites each behaviors[] entry; the export kept only the
    one the manual variant referenced and dropped the rest unannounced (#675)."""
    _declare_behaviors(project_dir, {"behaviors/local.xml": b"<root main='a'/>", "behaviors/second.xml": b"<root/>"})
    _patch_variant(
        project_dir, canonical.MANUAL_VARIANT, automation={"enabled": True, "behavior_tree": "behaviors/local.xml"}
    )
    out = tmp_path / "out.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=out)
    with zipfile.ZipFile(out) as archive:
        assert archive.read("runtime/behaviors/second.xml") == b"<root/>"
        assert archive.read("runtime/behaviors/local.xml") == b"<root main='a'/>"
        profile = yaml.safe_load(archive.read("machine-profile.yaml"))
        runtime_payload = yaml.safe_load(archive.read("runtime/anolis-runtime.yaml"))
    assert profile["behaviors"] == ["runtime/behaviors/local.xml", "runtime/behaviors/second.xml"]
    assert runtime_payload["automation"]["behavior_tree"] == "runtime/behaviors/local.xml"


def test_the_imported_profile_fixture_exports_its_declared_behavior(tmp_path: pathlib.Path) -> None:
    """The repo's imported-profile fixture declares one behavior its manual
    variant does not use; the package came out with none (#675)."""
    project_dir = tmp_path / "imported"
    shutil.copytree(pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "imported-profile", project_dir)
    out = tmp_path / "out.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=out)
    with zipfile.ZipFile(out) as archive:
        assert "runtime/behaviors/bioreactor_stir_dual_dosing.xml" in archive.namelist()
        profile = yaml.safe_load(archive.read("machine-profile.yaml"))
    assert profile["behaviors"] == ["runtime/behaviors/bioreactor_stir_dual_dosing.xml"]


def test_two_declared_behaviors_with_one_file_name_fail_the_export(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Both would land at runtime/behaviors/tree.xml, one over the other."""
    _declare_behaviors(project_dir, {"behaviors/a/tree.xml": b"<a/>", "behaviors/b/tree.xml": b"<b/>"})
    with pytest.raises(exporter.ExportError, match="share the file name tree.xml"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def _reference_behavior(project_dir: pathlib.Path, name: str, content: bytes) -> None:
    (project_dir / "behaviors" / name).write_bytes(content)
    _patch_variant(
        project_dir,
        canonical.MANUAL_VARIANT,
        automation={"enabled": True, "behavior_tree": f"behaviors/{name}"},
    )


def test_a_behavior_file_the_secret_scan_does_not_parse_is_carried_as_is(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The secret scan reads only YAML and JSON. A behavior file with another
    extension is not decoded, so bytes that are not UTF-8 do not fail the
    export (#674)."""
    _reference_behavior(project_dir, "tree.bt", b"\xff\xfe<root/>")
    out = tmp_path / "out.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=out)
    with zipfile.ZipFile(out) as archive:
        assert archive.read("runtime/behaviors/tree.bt") == b"\xff\xfe<root/>"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("tree.yaml", b"key: [unclosed"),
        ("tree.yml", b"\xff\xfe: x"),
        ("tree.json", b"{not json"),
    ],
)
def test_a_behavior_file_the_secret_scan_cannot_read_is_an_export_error(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, name: str, content: bytes
) -> None:
    """A YAML or JSON package file that is not UTF-8 or does not parse was a
    raw UnicodeDecodeError or YAMLError: a traceback from the CLI and a 500
    over HTTP, where both expect ExportError (#674)."""
    _reference_behavior(project_dir, name, content)
    with pytest.raises(exporter.ExportError, match=f"runtime/behaviors/{name}"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_missing_machine_profile_raises_export_error(tmp_path: pathlib.Path) -> None:
    project_dir = tmp_path / "no-profile"
    project_dir.mkdir()
    with pytest.raises(exporter.ExportError, match="Project file not found"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_malformed_machine_profile_raises_export_error(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    (project_dir / "machine-profile.yaml").write_text("{not: valid: yaml", encoding="utf-8")
    with pytest.raises(exporter.ExportError):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_absolute_behavior_tree_path_raises_export_error(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    abs_bt = str((tmp_path / "behaviors" / "local.xml").resolve())
    _patch_variant(
        project_dir,
        canonical.MANUAL_VARIANT,
        automation={"enabled": True, "behavior_tree": abs_bt},
    )
    with pytest.raises(exporter.ExportError, match="must be a relative path"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_missing_behavior_tree_file_raises_export_error(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    _patch_variant(
        project_dir,
        canonical.MANUAL_VARIANT,
        automation={"enabled": True, "behavior_tree": "behaviors/ghost.xml"},
    )
    with pytest.raises(exporter.ExportError, match="Behavior tree file not found"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_secret_leak_raises_export_error() -> None:
    with pytest.raises(exporter.ExportError, match="Secret-like token value leaked"):
        exporter._assert_no_secret_leak(
            {
                "providers/test.yaml": b"connection:\n  token: 'leaked-secret'\n",
            }
        )


# ---------------------------------------------------------------------------
# _latest_release_version
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_latest_release_version_strips_v_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})
    monkeypatch.setattr(releases.requests, "get", lambda *a, **k: _FakeResponse(200, {"tag_name": "v1.2.3"}))
    assert releases.latest_release_version("anolishq/some-repo") == "1.2.3"


def test_latest_release_version_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})
    monkeypatch.setattr(releases.requests, "get", lambda *a, **k: _FakeResponse(404, {}))
    assert releases.latest_release_version("anolishq/no-releases") is None


def test_latest_release_version_none_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})
    # The autouse fixture already makes requests.get raise RequestException.
    assert releases.latest_release_version("anolishq/offline") is None


def test_latest_release_version_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})
    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(200, {"tag_name": "v2.0.0"})

    monkeypatch.setattr(releases.requests, "get", _get)
    assert releases.latest_release_version("anolishq/cached") == "2.0.0"
    assert releases.latest_release_version("anolishq/cached") == "2.0.0"
    assert len(calls) == 1


def test_release_pins_env_seeds_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANOLIS_WB_RELEASE_PINS", '{"anolishq/anolis": "1.2.3", "anolishq/x": null}')
    assert releases._seed_from_env() == {"anolishq/anolis": "1.2.3", "anolishq/x": None}
    monkeypatch.setenv("ANOLIS_WB_RELEASE_PINS", "not-json")
    assert releases._seed_from_env() == {}
    monkeypatch.delenv("ANOLIS_WB_RELEASE_PINS")
    assert releases._seed_from_env() == {}


# ---------------------------------------------------------------------------
# The archive write (#675): a unique temp file beside the output, replaced
# onto it only once the archive is closed, and removed on any failure.
# ---------------------------------------------------------------------------

_ARCHIVE_FILES = {"a.txt": b"x" * 1000, "b.txt": b"y"}


def _leftover_temp_files(directory: pathlib.Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


# A ZipFile whose close failed tries again from __del__, after the file under
# it is closed; pytest reports that as an unraisable exception.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.parametrize("failing", ["writestr", "close"])
def test_a_failed_archive_write_leaves_no_temp_file_and_the_old_archive(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    out = tmp_path / "demo.anpkg"
    out.write_bytes(b"the previous export")

    def _fail(self: zipfile.ZipFile, *args: object, **kwargs: object) -> None:
        raise OSError(f"{failing} failed: disk full")

    monkeypatch.setattr(zipfile.ZipFile, failing, _fail)
    with pytest.raises(OSError, match=f"{failing} failed: disk full"):
        exporter._write_zip_deterministic(files=_ARCHIVE_FILES, out_path=out)

    assert _leftover_temp_files(tmp_path) == []
    assert out.read_bytes() == b"the previous export"


def test_a_stale_fixed_temp_path_does_not_stop_an_export(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "demo.anpkg"
    (tmp_path / "demo.anpkg.tmp").mkdir()

    exporter._write_zip_deterministic(files=_ARCHIVE_FILES, out_path=out)

    with zipfile.ZipFile(out) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == ["a.txt", "b.txt"]


def test_the_archive_keeps_the_mode_an_export_has_always_had(tmp_path: pathlib.Path) -> None:
    # open()'s 0o666 less the umask, as before the temp file became unique; a
    # mkstemp temp file would make it 0o600.
    umask = os.umask(0)
    os.umask(umask)
    out = tmp_path / "demo.anpkg"

    exporter._write_zip_deterministic(files=_ARCHIVE_FILES, out_path=out)

    assert stat.S_IMODE(out.stat().st_mode) == 0o666 & ~umask
    assert _leftover_temp_files(tmp_path) == []


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--config"], ["--config", "providers/x.yaml"]),
        (["--foo", "--config"], ["--foo", "--config", "providers/x.yaml"]),
        (["--config", "old.yaml"], ["--config", "providers/x.yaml"]),
        ([], ["--config", "providers/x.yaml"]),
    ],
)
def test_a_trailing_bare_config_gets_the_packaged_path_and_validates(
    tmp_path: pathlib.Path, args: list[str], expected: list[str]
) -> None:
    """A bare --config in the last position was left in place and a second
    --config pair appended, which the package validator rejects (#674)."""
    from anolis_workbench.core import package_validator

    runtime = {"providers": [{"id": "x", "args": list(args)}]}
    exporter._rewrite_provider_args(runtime)
    assert runtime["providers"][0]["args"] == expected

    (tmp_path / "providers").mkdir()
    (tmp_path / "providers" / "x.yaml").write_text("{}\n", encoding="utf-8")
    package_validator._validate_runtime_provider_args(package_root=tmp_path, runtime_payload=runtime)


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions, not root"
)
def test_a_behavior_file_that_cannot_be_read_is_an_export_error(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Review of #902: a behavior file with no read permission raised a raw
    PermissionError, a traceback from the CLI and a 500 over HTTP."""
    behavior = project_dir / "behaviors" / "local.xml"
    _patch_variant(
        project_dir, canonical.MANUAL_VARIANT, automation={"enabled": True, "behavior_tree": "behaviors/local.xml"}
    )
    behavior.chmod(0)
    try:
        with pytest.raises(exporter.ExportError, match="local.xml could not be read"):
            exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")
    finally:
        behavior.chmod(0o644)
