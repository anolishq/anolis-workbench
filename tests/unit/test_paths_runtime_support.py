"""Unit tests for path resolution and ANOLIS_DATA_DIR behavior."""

from __future__ import annotations

import importlib
import pathlib


def _reload_paths_module():
    import anolis_workbench.core.paths as paths_module

    return importlib.reload(paths_module)


def test_systems_root_uses_anolis_data_dir_override(monkeypatch, tmp_path: pathlib.Path) -> None:
    override = tmp_path / "custom-systems-root"
    monkeypatch.setenv("ANOLIS_DATA_DIR", str(override))

    paths_module = _reload_paths_module()

    assert paths_module.SYSTEMS_ROOT == override.resolve()
    assert paths_module.DATA_ROOT == override.resolve().parent


def test_resolve_data_path_prefers_data_root(monkeypatch, tmp_path: pathlib.Path) -> None:
    systems_root = tmp_path / "systems"
    data_root = systems_root.parent
    runtime_rel = pathlib.Path("bin") / "anolis-runtime"
    preferred_runtime = data_root / runtime_rel

    preferred_runtime.parent.mkdir(parents=True, exist_ok=True)
    preferred_runtime.write_text("runtime", encoding="utf-8")

    monkeypatch.setenv("ANOLIS_DATA_DIR", str(systems_root))
    paths_module = _reload_paths_module()

    resolved = paths_module.resolve_data_path(str(runtime_rel))
    assert resolved == preferred_runtime.resolve()


def test_resolve_data_path_uses_systems_root_when_present(monkeypatch, tmp_path: pathlib.Path) -> None:
    systems_root = tmp_path / "systems"
    runtime_rel = pathlib.Path("providers") / "anolis-provider-sim"
    systems_runtime = systems_root / runtime_rel

    systems_runtime.parent.mkdir(parents=True, exist_ok=True)
    systems_runtime.write_text("runtime", encoding="utf-8")

    monkeypatch.setenv("ANOLIS_DATA_DIR", str(systems_root))
    paths_module = _reload_paths_module()

    resolved = paths_module.resolve_data_path(str(runtime_rel))
    assert resolved == systems_runtime.resolve()


def test_resolve_data_path_returns_data_root_candidate_when_missing(monkeypatch, tmp_path: pathlib.Path) -> None:
    systems_root = tmp_path / "systems"
    runtime_rel = pathlib.Path("build") / "dev-release" / "core" / "anolis-runtime"

    monkeypatch.setenv("ANOLIS_DATA_DIR", str(systems_root))
    paths_module = _reload_paths_module()

    resolved = paths_module.resolve_data_path(str(runtime_rel))
    assert resolved == (systems_root.parent / runtime_rel).resolve()
