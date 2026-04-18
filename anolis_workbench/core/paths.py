"""Path helpers for Anolis Workbench."""

from __future__ import annotations

import os
import pathlib

CORE_DIR = pathlib.Path(__file__).resolve().parent
PACKAGE_DIR = CORE_DIR.parent

# In editable mode these resolve to source-tree assets. In installed mode they
# resolve to bundled package-data assets under the same package root.
_SOURCE_ASSET_ROOT = PACKAGE_DIR
_PACKAGED_ASSET_ROOT = PACKAGE_DIR

_DEFAULT_SYSTEMS_ROOT = pathlib.Path.home() / ".anolis" / "systems"


def _resolve_systems_root() -> pathlib.Path:
    """Resolve project storage root with optional env override."""
    override = os.getenv("ANOLIS_DATA_DIR")
    if override and override.strip():
        return pathlib.Path(override).expanduser().resolve()
    return _DEFAULT_SYSTEMS_ROOT.resolve()


SYSTEMS_ROOT = _resolve_systems_root()
DATA_ROOT = SYSTEMS_ROOT.parent


def _select_asset_path(source_path: pathlib.Path, packaged_path: pathlib.Path) -> pathlib.Path:
    """Prefer source-tree assets in editable/dev mode, else use package data."""
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


def resolve_data_path(path_value: str) -> pathlib.Path:
    """Resolve executable/config paths under ANOLIS_DATA_DIR roots."""
    path = pathlib.Path(path_value).expanduser()
    if path.is_absolute():
        return path

    preferred = (DATA_ROOT / path).resolve()
    if preferred.exists():
        return preferred

    systems_relative = (SYSTEMS_ROOT / path).resolve()
    if systems_relative.exists():
        return systems_relative

    return preferred
