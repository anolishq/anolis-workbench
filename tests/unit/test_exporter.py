"""Unit tests for deterministic handoff package export core."""

from __future__ import annotations

import io
import json
import pathlib
import zipfile

import pytest
import requests
import yaml

from anolis_workbench.core import exporter, releases


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


def test_build_package_is_deterministic_and_rewrites_runtime_paths(tmp_path: pathlib.Path) -> None:
    project_dir = _make_project(tmp_path, name="export-deterministic")
    out_a = tmp_path / "a.anpkg"
    out_b = tmp_path / "b.anpkg"

    exporter.build_package(project_dir=project_dir, out_path=out_a)
    exporter.build_package(project_dir=project_dir, out_path=out_b)

    data_a = out_a.read_bytes()
    data_b = out_b.read_bytes()
    assert data_a == data_b

    with zipfile.ZipFile(io.BytesIO(data_a), mode="r") as archive:
        members = sorted(archive.namelist())
        assert members == sorted(
            [
                "machine-profile.yaml",
                "meta/checksums.sha256",
                "meta/provenance.json",
                "providers/sim0.yaml",
                "runtime/anolis-runtime.yaml",
                "runtime/behaviors/local.xml",
            ]
        )

        runtime_payload = yaml.safe_load(archive.read("runtime/anolis-runtime.yaml"))
        assert runtime_payload["providers"][0]["args"] == ["--config", "providers/sim0.yaml"]
        assert runtime_payload["automation"]["behavior_tree"] == "runtime/behaviors/local.xml"
        assert "token" not in runtime_payload.get("telemetry", {}).get("influxdb", {})
        assert "influx_token" not in runtime_payload.get("telemetry", {})

        machine_profile = yaml.safe_load(archive.read("machine-profile.yaml"))
        assert machine_profile["runtime_profiles"]["manual"] == "runtime/anolis-runtime.yaml"
        assert machine_profile["providers"]["sim0"]["config"] == "providers/sim0.yaml"
        assert machine_profile["behaviors"] == ["runtime/behaviors/local.xml"]
        assert machine_profile["components"]["runtime"] == {"repo": "anolishq/anolis", "version": "0.1.26"}
        assert machine_profile["components"]["providers"]["sim"] == {
            "repo": "anolishq/anolis-provider-sim",
            "version": "0.2.1",
        }

        provenance = json.loads(archive.read("meta/provenance.json").decode("utf-8"))
        assert provenance["exported_at"] == "2026-04-16T19:01:02Z"
        assert provenance["package_format_version"] == 1
        assert provenance["source_project"] == "export-deterministic"


def _make_project(tmp_path: pathlib.Path, *, name: str) -> pathlib.Path:
    system = {
        "schema_version": 1,
        "meta": {
            "name": name,
            "created": "2026-04-16T19:01:02.999999+00:00",
            "template": "sim-quickstart-fixture",
        },
        "topology": {
            "runtime": {
                "name": "anolis-main",
                "http_port": 8080,
                "http_bind": "127.0.0.1",
                "cors_origins": ["http://localhost:3000", "http://127.0.0.1:3000"],
                "cors_allow_credentials": False,
                "shutdown_timeout_ms": 2000,
                "startup_timeout_ms": 30000,
                "polling_interval_ms": 500,
                "log_level": "info",
                "telemetry": {
                    "enabled": True,
                    "influxdb": {
                        "url": "http://localhost:8086",
                        "org": "anolis",
                        "bucket": "anolis",
                        "token": "super-secret",
                    },
                },
                "automation_enabled": True,
                "behavior_tree_path": "behaviors/local.xml",
                "providers": [
                    {
                        "id": "sim0",
                        "kind": "sim",
                        "timeout_ms": 5000,
                        "hello_timeout_ms": 2000,
                        "ready_timeout_ms": 10000,
                        "restart_policy": {"enabled": False},
                    }
                ],
            },
            "providers": {
                "sim0": {
                    "kind": "sim",
                    "provider_name": "sim0",
                    "startup_policy": "degraded",
                    "simulation_mode": "non_interacting",
                    "tick_rate_hz": 10.0,
                    "devices": [
                        {"id": "tempctl0", "type": "tempctl", "initial_temp": 25.0},
                        {"id": "motorctl0", "type": "motorctl", "max_speed": 3000.0},
                    ],
                }
            },
        },
        "paths": {
            "runtime_executable": "build/dev-release/core/anolis-runtime",
            "providers": {
                "sim0": {
                    "executable": "../anolis-provider-sim/build/dev-release/anolis-provider-sim",
                }
            },
        },
    }

    project_dir = tmp_path / name
    project_dir.mkdir(parents=True, exist_ok=True)

    behavior_dir = project_dir / "behaviors"
    behavior_dir.mkdir(parents=True, exist_ok=True)
    (behavior_dir / "local.xml").write_text("<root />\n", encoding="utf-8")

    (project_dir / "system.json").write_text(json.dumps(system, indent=2), encoding="utf-8")
    return project_dir


def test_missing_system_json_raises_export_error(tmp_path: pathlib.Path) -> None:
    project_dir = tmp_path / "no-system"
    project_dir.mkdir()
    with pytest.raises(exporter.ExportError, match="Project file not found"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_malformed_system_json_raises_export_error(tmp_path: pathlib.Path) -> None:
    project_dir = tmp_path / "bad-json"
    project_dir.mkdir()
    (project_dir / "system.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="Failed reading"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_absolute_behavior_tree_path_raises_export_error(tmp_path: pathlib.Path) -> None:
    project_dir = _make_project(tmp_path, name="abs-bt-test")
    system_path = project_dir / "system.json"
    system = json.loads(system_path.read_text(encoding="utf-8"))
    # Use an absolute path (drive + path is absolute on all platforms)
    abs_bt = str((tmp_path / "behaviors" / "local.xml").resolve())
    system["topology"]["runtime"]["behavior_tree_path"] = abs_bt
    system_path.write_text(json.dumps(system, indent=2), encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="must be a relative path"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_missing_behavior_tree_file_raises_export_error(tmp_path: pathlib.Path) -> None:
    project_dir = _make_project(tmp_path, name="missing-bt-test")
    system_path = project_dir / "system.json"
    system = json.loads(system_path.read_text(encoding="utf-8"))
    system["topology"]["runtime"]["behavior_tree_path"] = "behaviors/ghost.xml"
    system_path.write_text(json.dumps(system, indent=2), encoding="utf-8")
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


# ---------------------------------------------------------------------------
# _build_machine_profile — component pin resolution
# ---------------------------------------------------------------------------


def _system_with_kinds(kinds: dict[str, str]) -> dict:
    return {
        "meta": {"name": "Test Machine"},
        "topology": {"providers": {pid: {"kind": kind} for pid, kind in kinds.items()}},
    }


def test_build_machine_profile_pins_released_provider() -> None:
    profile = exporter._build_machine_profile(
        system=_system_with_kinds({"sim0": "sim"}),
        project_name="test-machine",
        provider_ids=["sim0"],
        behavior_rel_paths={},
    )
    compat = profile["compatibility"]["providers"]["sim0"]
    assert compat["strategy"] == "pinned-ref"
    assert compat["version"] == "0.2.1"
    assert profile["components"]["runtime"] == {"repo": "anolishq/anolis", "version": "0.1.26"}
    # components are keyed by kind, not instance id
    assert profile["components"]["providers"]["sim"] == {
        "repo": "anolishq/anolis-provider-sim",
        "version": "0.2.1",
    }


def test_build_machine_profile_falls_back_for_unreleased_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        releases,
        "_RELEASE_CACHE",
        {"anolishq/anolis": "0.1.26", "anolishq/anolis-provider-custom": None},
    )
    profile = exporter._build_machine_profile(
        system=_system_with_kinds({"custom0": "custom"}),
        project_name="test-machine",
        provider_ids=["custom0"],
        behavior_rel_paths={},
    )
    compat = profile["compatibility"]["providers"]["custom0"]
    assert compat["strategy"] == "local-build"
    assert compat["version"] == "unspecified"
    # No downloadable provider → no components section at all.
    assert "components" not in profile


def test_build_machine_profile_falls_back_when_kind_unknown() -> None:
    profile = exporter._build_machine_profile(
        system={},  # no topology → no kind for the instance
        project_name="test-machine",
        provider_ids=["mystery0"],
        behavior_rel_paths={},
    )
    assert profile["compatibility"]["providers"]["mystery0"]["strategy"] == "local-build"
    assert "components" not in profile


def test_build_machine_profile_mixes_released_and_unreleased_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        releases,
        "_RELEASE_CACHE",
        {
            "anolishq/anolis": "0.1.26",
            "anolishq/anolis-provider-sim": "0.2.1",
            "anolishq/anolis-provider-custom": None,
        },
    )
    profile = exporter._build_machine_profile(
        system=_system_with_kinds({"sim0": "sim", "custom0": "custom"}),
        project_name="test-machine",
        provider_ids=["sim0", "custom0"],
        behavior_rel_paths={},
    )
    assert profile["compatibility"]["providers"]["sim0"]["strategy"] == "pinned-ref"
    assert profile["compatibility"]["providers"]["custom0"]["strategy"] == "local-build"
    assert sorted(profile["components"]["providers"]) == ["sim"]


def test_build_machine_profile_omits_components_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        releases,
        "_RELEASE_CACHE",
        {"anolishq/anolis": None, "anolishq/anolis-provider-sim": None},
    )
    profile = exporter._build_machine_profile(
        system=_system_with_kinds({"sim0": "sim"}),
        project_name="test-machine",
        provider_ids=["sim0"],
        behavior_rel_paths={},
    )
    assert profile["compatibility"]["providers"]["sim0"]["strategy"] == "local-build"
    assert "components" not in profile
