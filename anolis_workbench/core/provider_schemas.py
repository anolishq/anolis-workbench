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
