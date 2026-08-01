"""Vendored provider config-schema envelope registry (#270).

Each provider publishes its config contract as a `--config-schema` envelope
(anolis executable profile v1 §2) on its GitHub releases; the workbench vendors
those envelopes under ``anolis_workbench/schemas/providers/`` via the
sha256-locked sync/verify scripts. This module is the single access point: the
set of known provider kinds IS the set of vendored envelopes.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from anolis_workbench.core import paths as paths_module

_ENVELOPE_SUFFIX = ".config-schema.json"

_cache_lock = threading.Lock()
_envelope_cache: dict[str, dict[str, Any]] | None = None


def _validate_envelope_shape(kind: str, doc: Any) -> dict[str, Any]:
    """Belt-and-suspenders: the sync script validated this at vendor time, but a
    packaging or hand-edit mistake should fail loudly, not render a broken form."""
    version = doc.get("config_schema_version") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"provider schema envelope '{kind}' must carry an integer config_schema_version >= 1")
    if not isinstance(doc.get("schema"), dict):
        raise ValueError(f"provider schema envelope '{kind}' must carry a JSON-object 'schema'")
    return doc


def _load_envelopes() -> dict[str, dict[str, Any]]:
    global _envelope_cache
    with _cache_lock:
        if _envelope_cache is None:
            envelopes: dict[str, dict[str, Any]] = {}
            schemas_dir = paths_module.PROVIDER_SCHEMAS_DIR
            if schemas_dir.is_dir():
                for path in sorted(schemas_dir.glob(f"*{_ENVELOPE_SUFFIX}")):
                    kind = path.name[: -len(_ENVELOPE_SUFFIX)]
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    envelopes[kind] = _validate_envelope_shape(kind, doc)
            _envelope_cache = envelopes
        return _envelope_cache


def available_kinds() -> list[str]:
    """Provider kinds with a vendored config-schema envelope."""
    return sorted(_load_envelopes().keys())


def get_envelope(kind: str) -> dict[str, Any] | None:
    """The full envelope for a kind, or None when unknown."""
    return _load_envelopes().get(kind)


def all_envelopes() -> dict[str, dict[str, Any]]:
    """kind -> envelope, for the /api/provider-schemas route."""
    return dict(_load_envelopes())


# ---------------------------------------------------------------------------
# x-anolis-unique enforcement (#270)
#
# JSON Schema's uniqueItems compares raw values, so 10 and "0x0A" pass despite
# naming the same I2C address. Providers annotate uniqueness-bearing fields
# with x-anolis-unique (on an array schema for scalar items, or on a property
# of the items schema for arrays of objects) and dual-form fields with
# x-anolis-type: i2c_address; this walker enforces them value-normalized.
# ---------------------------------------------------------------------------


def _normalize_unique_value(value: Any, field_schema: dict[str, Any]) -> Any:
    if field_schema.get("x-anolis-type") == "i2c_address":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip(), 0)
            except ValueError:
                return value
    return value


def _duplicates(values: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    dups: list[Any] = []
    for value in values:
        try:
            if value in seen:
                if value not in dups:
                    dups.append(value)
            else:
                seen.add(value)
        except TypeError:  # unhashable (malformed doc) — schema validation reports it
            continue
    return dups


def unique_violations(schema: Any, instance: Any, path: str = "$") -> list[dict[str, str]]:
    """Walk schema+instance together, returning x-anolis-unique violations.

    Each violation is {"path": <json path of the array>, "message": ...}.
    """
    violations: list[dict[str, str]] = []
    if not isinstance(schema, dict):
        return violations

    if isinstance(instance, dict):
        props = schema.get("properties")
        for key, value in instance.items():
            if isinstance(props, dict) and isinstance(props.get(key), dict):
                violations.extend(unique_violations(props[key], value, f"{path}.{key}"))
        return violations

    if isinstance(instance, list):
        items_schema = schema.get("items")
        if schema.get("x-anolis-unique") is True:
            field_schema = items_schema if isinstance(items_schema, dict) else {}
            normalized = [_normalize_unique_value(v, field_schema) for v in instance]
            for dup in _duplicates(normalized):
                violations.append(
                    {
                        "path": path,
                        "message": f"duplicate value {dup!r} in {path} (values must be unique)",
                    }
                )
        if isinstance(items_schema, dict):
            item_props = items_schema.get("properties")
            if isinstance(item_props, dict):
                for key, prop_schema in item_props.items():
                    if not (isinstance(prop_schema, dict) and prop_schema.get("x-anolis-unique") is True):
                        continue
                    values = [
                        _normalize_unique_value(item[key], prop_schema)
                        for item in instance
                        if isinstance(item, dict) and key in item
                    ]
                    for dup in _duplicates(values):
                        violations.append(
                            {
                                "path": path,
                                "message": f"duplicate {key} {dup!r} in {path} ({key} must be unique)",
                            }
                        )
            for idx, item in enumerate(instance):
                violations.extend(unique_violations(items_schema, item, f"{path}[{idx}]"))
        return violations

    return violations
