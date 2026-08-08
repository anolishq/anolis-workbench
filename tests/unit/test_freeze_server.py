"""Guards for the frozen-sidecar build hints.

`DEFAULT_HIDDEN_IMPORTS` tells PyInstaller about modules that are only reached
dynamically at runtime. It used to be hand-maintained, and it drifted: three
entries named modules that no longer existed while ten real ones were missing.

The phantom entries were harmless — PyInstaller warns and moves on. The
omissions were not: a module absent from that list can be dropped from the
frozen bundle and only surface as an ImportError on a user's machine, long after
a build that looked clean. These tests pin the derivation so the list cannot
silently fall behind the tree again.
"""

from __future__ import annotations

import pathlib

import freeze_server

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _module_path(dotted: str) -> pathlib.Path:
    return REPO_ROOT / pathlib.Path(*dotted.split(".")).with_suffix(".py")


def test_every_hidden_import_names_a_real_module() -> None:
    """No phantom entries — the failure mode that started this."""
    missing = [name for name in freeze_server.DEFAULT_HIDDEN_IMPORTS if not _module_path(name).is_file()]
    assert not missing, f"hidden imports name modules that do not exist: {missing}"


def test_every_runtime_loaded_module_is_hinted() -> None:
    """No omissions — the failure mode that actually breaks a frozen build."""
    listed = set(freeze_server.DEFAULT_HIDDEN_IMPORTS)
    for package in freeze_server._HIDDEN_IMPORT_PACKAGES:
        package_dir = REPO_ROOT / pathlib.Path(*package.split("."))
        for module in package_dir.glob("*.py"):
            if module.stem.startswith("__"):
                continue
            assert f"{package}.{module.stem}" in listed, f"{module} is not hinted to PyInstaller"


def test_discovery_fails_loudly_on_a_renamed_package() -> None:
    """A package rename must break the build, not quietly hint nothing.

    Silently returning an empty list would produce a frozen sidecar missing
    every dynamically-loaded route — the exact class of failure this guards.
    """
    original = freeze_server._HIDDEN_IMPORT_PACKAGES
    freeze_server._HIDDEN_IMPORT_PACKAGES = ("anolis_workbench.core.renamed_away",)
    try:
        raised = False
        try:
            freeze_server._discover_hidden_imports()
        except SystemExit:
            raised = True
        assert raised, "a missing package directory must abort the build"
    finally:
        freeze_server._HIDDEN_IMPORT_PACKAGES = original
