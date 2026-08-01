"""system.json schema migrations.

v1 -> v2 (#270): provider config moves to the PROVIDER-NATIVE shape — each
``topology.providers`` entry becomes ``{kind, config}`` where ``config`` is
exactly the document the provider's ``--config-schema`` envelope describes
(and what the renderer emits verbatim). The transform is the old per-kind
renderer logic relocated here: what v1 stored field-by-field and the renderer
reassembled, v2 stores assembled. ``paths.providers[pid].bus_path`` moves into
``config.hardware.bus_path``; I2C addresses keep their authored form (hex
strings like "0x0A" — provider binaries parse both).

Migration is applied on load (persisted by projects.get_project, defensively
elsewhere) so every consumer downstream of a loader only ever sees v2.
"""

from __future__ import annotations

import copy
from typing import Any

CURRENT_SCHEMA_VERSION = 2


def migrate_system(system: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (document at the current schema version, whether it changed).

    v1 documents are migrated to v2; anything else is returned untouched
    (schema validation is responsible for rejecting unknown versions loudly).
    """
    if system.get("schema_version") != 1:
        return system, False
    return _migrate_v1_to_v2(system), True


def _migrate_v1_to_v2(system: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(system)
    out["schema_version"] = 2

    paths = out.get("paths", {})
    provider_paths = paths.get("providers", {}) if isinstance(paths, dict) else {}

    topology = out.get("topology", {})
    providers = topology.get("providers", {}) if isinstance(topology, dict) else {}
    for pid, pdata in list(providers.items()):
        if not isinstance(pdata, dict):
            continue
        kind = pdata.get("kind")
        path_data = provider_paths.get(pid, {}) if isinstance(provider_paths, dict) else {}
        if kind == "sim":
            config = _sim_config(pdata)
        elif kind == "bread":
            config = _bus_provider_config(pdata, path_data, synthesize_addresses=True)
        elif kind == "ezo":
            config = _bus_provider_config(pdata, path_data, synthesize_addresses=False)
        else:
            # Unknown/custom kinds carried no renderable v1 fields; keep any
            # pre-existing config object so the migration stays total.
            existing = pdata.get("config")
            config = existing if isinstance(existing, dict) else {}
        providers[pid] = {"kind": kind, "config": config}

    if isinstance(provider_paths, dict):
        for entry in provider_paths.values():
            if isinstance(entry, dict):
                entry.pop("bus_path", None)

    return out


def _sim_config(pdata: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}

    if "provider_name" in pdata:
        config["provider"] = {"name": pdata["provider_name"]}

    if "startup_policy" in pdata:
        config["startup_policy"] = pdata["startup_policy"]

    devices = []
    for dev in pdata.get("devices", []):
        d: dict[str, Any] = {"id": dev.get("id"), "type": dev.get("type")}
        # Preserve per-device extras (initial_temp, max_speed, ...) — the sim
        # schema leaves device items open for type-specific fields.
        for key, value in dev.items():
            if key not in d:
                d[key] = value
        devices.append(d)
    config["devices"] = devices

    simulation_cfg = pdata.get("simulation")
    if not isinstance(simulation_cfg, dict):
        simulation_cfg = {}
    mode = pdata.get("simulation_mode") or simulation_cfg.get("mode") or "non_interacting"
    simulation: dict[str, Any] = {"mode": mode}
    tick_rate_hz = pdata.get("tick_rate_hz", simulation_cfg.get("tick_rate_hz"))
    if mode != "inert" and tick_rate_hz is not None:
        simulation["tick_rate_hz"] = tick_rate_hz
    for key in ("physics_config", "ambient_temp_c", "ambient_signal_path"):
        if key in simulation_cfg:
            simulation[key] = simulation_cfg[key]
    config["simulation"] = simulation

    return config


def _bus_provider_config(
    pdata: dict[str, Any],
    path_data: dict[str, Any],
    *,
    synthesize_addresses: bool,
) -> dict[str, Any]:
    config: dict[str, Any] = {}

    if "provider_name" in pdata:
        config["provider"] = {"name": pdata["provider_name"]}

    hardware: dict[str, Any] = {"bus_path": path_data.get("bus_path", "")}
    for key in ("query_delay_us", "timeout_ms", "retry_count"):
        if key in pdata:
            hardware[key] = pdata[key]
    config["hardware"] = hardware

    discovery: dict[str, Any] = {"mode": "manual"}
    if synthesize_addresses:
        addresses = [dev["address"] for dev in pdata.get("devices", []) if "address" in dev]
        if addresses:
            discovery["addresses"] = addresses
    config["discovery"] = discovery

    devices = []
    for dev in pdata.get("devices", []):
        d: dict[str, Any] = {"id": dev.get("id"), "type": dev.get("type")}
        if "label" in dev:
            d["label"] = dev["label"]
        if "address" in dev:
            d["address"] = dev["address"]
        devices.append(d)
    config["devices"] = devices

    return config
