"""
renderer.py — system.json → YAML config renderer.

Pure-function module. No file I/O. No subprocess calls. No HTTP.
Takes a system.json dict, returns a dict of rendered YAML strings keyed by
relative output path (e.g. "anolis-runtime.yaml", "providers/sim0.yaml").
"""

import yaml


def render(system: dict, project_name: str, *, systems_dir_name: str = "systems") -> dict[str, str]:
    """
    Render a system.json dict into YAML config strings.

    Args:
        system:       Parsed system.json dict (schema_version 2).
        project_name: Name of the project directory under systems/.
                      Used to build the provider config file paths that the
                      runtime will pass to each provider via --config.
        systems_dir_name:
                      Root directory name that contains project folders
                      (default: "systems"). Used for runtime provider
                      config path generation.

    Returns:
        {
            "anolis-runtime.yaml": "<yaml content>",
            "providers/<provider_id>.yaml": "<yaml content>",
            ...
        }
    """
    topology = system["topology"]
    paths = system["paths"]
    rt = topology["runtime"]
    provider_paths = paths.get("providers", {})

    outputs: dict[str, str] = {}

    # -------------------------------------------------------------------------
    # anolis-runtime.yaml
    # -------------------------------------------------------------------------
    runtime_doc: dict = {}

    # runtime section
    runtime_section: dict = {}
    if rt.get("name"):
        runtime_section["name"] = rt["name"]
    if "shutdown_timeout_ms" in rt:
        runtime_section["shutdown_timeout_ms"] = rt["shutdown_timeout_ms"]
    if "startup_timeout_ms" in rt:
        runtime_section["startup_timeout_ms"] = rt["startup_timeout_ms"]
    if runtime_section:
        runtime_doc["runtime"] = runtime_section

    # http section
    http_section: dict = {
        "enabled": True,
        "bind": rt.get("http_bind", "127.0.0.1"),
        "port": rt["http_port"],
        "cors_allow_credentials": rt.get("cors_allow_credentials", False),
    }
    if "cors_origins" in rt:
        http_section["cors_allowed_origins"] = rt["cors_origins"]
    runtime_doc["http"] = http_section

    # providers section
    provider_list = []
    for p in rt.get("providers", []):
        pid = p["id"]
        config_arg = f"{systems_dir_name}/{project_name}/providers/{pid}.yaml"
        path_entry = provider_paths.get(pid, {})
        entry: dict = {
            "id": pid,
            "command": path_entry.get("executable", ""),
            "args": ["--config", config_arg],
            "timeout_ms": p.get("timeout_ms", 5000),
        }
        if "hello_timeout_ms" in p:
            entry["hello_timeout_ms"] = p["hello_timeout_ms"]
        if "ready_timeout_ms" in p:
            entry["ready_timeout_ms"] = p["ready_timeout_ms"]

        rp = p.get("restart_policy")
        if rp is not None:
            rp_out: dict = {"enabled": rp.get("enabled", False)}
            if rp.get("enabled"):
                rp_out["max_attempts"] = rp["max_attempts"]
                rp_out["backoff_ms"] = rp["backoff_ms"]
                rp_out["timeout_ms"] = rp["timeout_ms"]
                if "success_reset_ms" in rp:
                    rp_out["success_reset_ms"] = rp["success_reset_ms"]
            entry["restart_policy"] = rp_out

        provider_list.append(entry)
    runtime_doc["providers"] = provider_list

    # polling
    if "polling_interval_ms" in rt:
        runtime_doc["polling"] = {"interval_ms": rt["polling_interval_ms"]}

    # telemetry
    telemetry_cfg = _runtime_telemetry(rt)
    runtime_doc["telemetry"] = {"enabled": telemetry_cfg["enabled"]}
    if telemetry_cfg["enabled"] and telemetry_cfg["influxdb"]:
        runtime_doc["telemetry"]["influxdb"] = telemetry_cfg["influxdb"]

    # automation
    bt_path = rt.get("behavior_tree_path") or rt.get("behavior_tree")
    if bt_path:
        runtime_doc["automation"] = {"enabled": True, "behavior_tree": bt_path}
    else:
        runtime_doc["automation"] = {"enabled": rt.get("automation_enabled", False)}

    # logging
    runtime_doc["logging"] = {"level": rt.get("log_level", "info")}

    outputs["anolis-runtime.yaml"] = yaml.dump(runtime_doc, default_flow_style=False, sort_keys=False)

    # -------------------------------------------------------------------------
    # Per-provider config files — passthrough (#270)
    #
    # system.json v2 stores each provider's config in the provider-native
    # shape its --config-schema envelope describes, so rendering is a verbatim
    # YAML dump. No per-kind knowledge lives here anymore.
    # -------------------------------------------------------------------------
    for pid, pdata in topology.get("providers", {}).items():
        config = pdata.get("config")
        # Skip missing AND empty configs: an unknown-kind provider migrates to
        # config {}, and emitting "{}" here would shadow the exporter/deploy
        # fallback that packages a hand-authored providers/<pid>.yaml.
        if not isinstance(config, dict) or not config:
            continue
        outputs[f"providers/{pid}.yaml"] = yaml.dump(config, default_flow_style=False, sort_keys=False)

    return outputs


def _runtime_telemetry(rt: dict) -> dict:
    telemetry = rt.get("telemetry")
    if not isinstance(telemetry, dict):
        telemetry = {}

    enabled = telemetry.get("enabled")
    if enabled is None:
        enabled = rt.get("telemetry_enabled", False)

    influx_in = telemetry.get("influxdb")
    influx_cfg = influx_in if isinstance(influx_in, dict) else {}
    influx_out: dict = {}
    for key in (
        "url",
        "org",
        "bucket",
        "token",
        "batch_size",
        "flush_interval_ms",
        "max_retry_buffer_size",
        "queue_size",
    ):
        value = influx_cfg.get(key)
        if value not in (None, ""):
            influx_out[key] = value

    return {"enabled": bool(enabled), "influxdb": influx_out}
