"""Path helpers for System Composer.

This module keeps repo-relative paths centralized so backend modules do not
duplicate hardcoded string literals or depend on the caller's CWD.
"""

from __future__ import annotations

import os
import pathlib
import warnings

BACKEND_DIR = pathlib.Path(__file__).resolve().parent
COMPOSER_DIR = BACKEND_DIR.parent
REPO_ROOT = COMPOSER_DIR.parent

_SOURCE_ASSET_ROOT = COMPOSER_DIR
_PACKAGED_ASSET_ROOT = BACKEND_DIR

_LEGACY_SYSTEMS_ROOT = REPO_ROOT / "systems"
_DEFAULT_SYSTEMS_ROOT = pathlib.Path.home() / ".anolis" / "systems"
_LEGACY_PATH_WARNING_EMITTED = False


def _resolve_systems_root() -> pathlib.Path:
    """Resolve project storage root with env override + developer fallback."""
    override = os.getenv("ANOLIS_DATA_DIR")
    if override and override.strip():
        return pathlib.Path(override).expanduser().resolve()
    if _LEGACY_SYSTEMS_ROOT.exists():
        return _LEGACY_SYSTEMS_ROOT.resolve()
    return _DEFAULT_SYSTEMS_ROOT.resolve()


SYSTEMS_ROOT = _resolve_systems_root()
DATA_ROOT = SYSTEMS_ROOT.parent


def _select_asset_path(source_path: pathlib.Path, packaged_path: pathlib.Path) -> pathlib.Path:
    """Prefer source-tree assets in editable/dev mode, fallback to package data."""
    if source_path.exists():
        return source_path
    return packaged_path


TEMPLATES_ROOT = _select_asset_path(
    _SOURCE_ASSET_ROOT / "templates",
    _PACKAGED_ASSET_ROOT / "templates",
)
FRONTEND_DIR = _select_asset_path(
    _SOURCE_ASSET_ROOT / "frontend",
    _PACKAGED_ASSET_ROOT / "frontend",
)
CATALOG_PATH = _select_asset_path(
    _SOURCE_ASSET_ROOT / "catalog" / "providers.json",
    _PACKAGED_ASSET_ROOT / "catalog" / "providers.json",
)
SYSTEM_SCHEMA_PATH = _select_asset_path(
    _SOURCE_ASSET_ROOT / "schema" / "system.schema.json",
    _PACKAGED_ASSET_ROOT / "schema" / "system.schema.json",
)


def resolve_repo_path(path_value: str) -> pathlib.Path:
    """Resolve executable/config paths with ANOLIS_DATA_DIR-first semantics."""
    global _LEGACY_PATH_WARNING_EMITTED

    path = pathlib.Path(path_value).expanduser()
    if path.is_absolute():
        return path

    preferred = (DATA_ROOT / path).resolve()
    if preferred.exists():
        return preferred

    systems_relative = (SYSTEMS_ROOT / path).resolve()
    if systems_relative.exists():
        return systems_relative

    legacy = (REPO_ROOT / path).resolve()
    if legacy.exists():
        if not _LEGACY_PATH_WARNING_EMITTED:
            warnings.warn(
                "Resolved executable path against legacy repo root. "
                "Update system paths to ANOLIS_DATA_DIR-relative or absolute paths.",
                RuntimeWarning,
                stacklevel=2,
            )
            _LEGACY_PATH_WARNING_EMITTED = True
        return legacy

    return preferred
