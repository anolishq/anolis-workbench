"""Unit tests for deterministic handoff package export core."""

from __future__ import annotations

import io
import json
import pathlib
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
            ]
        )

        runtime_payload = yaml.safe_load(archive.read("runtime/anolis-runtime.yaml"))
        assert runtime_payload["providers"][0]["args"] == ["--config", "providers/sim0.yaml"]
        assert "token" not in runtime_payload.get("telemetry", {}).get("influxdb", {})
        assert "influx_token" not in runtime_payload.get("telemetry", {})

        profile = yaml.safe_load(archive.read("machine-profile.yaml"))
        assert profile["runtime_profiles"] == {"manual": "runtime/anolis-runtime.yaml"}
        assert profile["providers"]["sim0"]["config"] == "providers/sim0.yaml"

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
