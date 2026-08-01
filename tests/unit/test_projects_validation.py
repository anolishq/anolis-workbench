"""Unit tests for backend/projects.py save-time validation behavior."""

import json
import pathlib

import pytest

from anolis_workbench.core import projects


def _load_template(name: str) -> dict:
    template_path = (
        pathlib.Path(__file__).parent.parent.parent / "anolis_workbench" / "templates" / name / "system.json"
    )
    return json.loads(template_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_save_project_rejects_schema_invalid_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", tmp_path / "systems")

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("invalid-schema", {"schema_version": 1})

    errors = exc_info.value.errors
    assert any(err.get("source") == "schema" for err in errors), errors
    assert not (tmp_path / "systems" / "invalid-schema" / "system.json").exists()


def test_save_project_rejects_semantic_invalid_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", tmp_path / "systems")
    system = _load_template("sim-quickstart")
    system["topology"]["runtime"]["providers"].append(
        {
            "id": "sim0",
            "kind": "sim",
            "timeout_ms": 5000,
            "hello_timeout_ms": 2000,
            "ready_timeout_ms": 10000,
            "restart_policy": {"enabled": False},
        }
    )

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("invalid-semantic", system)

    errors = exc_info.value.errors
    assert any(err.get("source") == "semantic" for err in errors), errors
    assert any("Duplicate provider IDs" in err.get("message", "") for err in errors), errors
    assert not (tmp_path / "systems" / "invalid-semantic" / "system.json").exists()


def test_save_project_rejects_unknown_provider_kind(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Kinds are the set of vendored config-schema envelopes; anything else is rejected."""
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", tmp_path / "systems")
    system = _load_template("sim-quickstart")
    system["topology"]["runtime"]["providers"].append(
        {
            "id": "custom0",
            "kind": "custom",
            "timeout_ms": 5000,
            "hello_timeout_ms": 2000,
            "ready_timeout_ms": 10000,
            "restart_policy": {"enabled": False},
        }
    )
    system["topology"]["providers"]["custom0"] = {"kind": "custom", "config": {}}
    system["paths"]["providers"]["custom0"] = {"executable": "../custom-provider/build/provider"}

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("invalid-custom", system)

    errors = exc_info.value.errors
    unknown = [err for err in errors if err.get("code") == "provider.unknown_kind"]
    assert unknown, errors
    assert unknown[0]["path"] == "$.topology.providers.custom0.kind"
    assert "custom" in unknown[0]["message"]
    assert not (tmp_path / "systems" / "invalid-custom" / "system.json").exists()


def test_validate_provider_config_envelope_failure_matrix() -> None:
    """Representative envelope violations must surface as provider-schema errors
    with paths anchored under the provider's config."""
    system = _load_template("mixed-bus-mock")
    bread = system["topology"]["providers"]["bread0"]["config"]

    # 1. Missing required section (hardware)
    del bread["hardware"]
    errors = projects.validate_system_payload(system)
    assert any(e["code"] == "provider.schema" and e["path"] == "$.topology.providers.bread0.config" for e in errors), (
        errors
    )

    # 2. Wrong type deep in the config
    system = _load_template("mixed-bus-mock")
    system["topology"]["providers"]["bread0"]["config"]["hardware"]["timeout_ms"] = "fast"
    errors = projects.validate_system_payload(system)
    assert any(
        e["code"] == "provider.schema" and e["path"] == "$.topology.providers.bread0.config.hardware.timeout_ms"
        for e in errors
    ), errors

    # 3. Conditional: discovery.mode manual requires addresses
    system = _load_template("mixed-bus-mock")
    del system["topology"]["providers"]["bread0"]["config"]["discovery"]["addresses"]
    errors = projects.validate_system_payload(system)
    assert any(
        e["code"] == "provider.schema" and e["path"].startswith("$.topology.providers.bread0.config.discovery")
        for e in errors
    ), errors

    # 4. Out-of-range I2C address (pattern/anyOf)
    system = _load_template("mixed-bus-mock")
    system["topology"]["providers"]["ezo0"]["config"]["devices"][0]["address"] = "0xFF"
    errors = projects.validate_system_payload(system)
    assert any(e["code"] == "provider.schema" and "devices[0]" in e["path"] and "ezo0" in e["path"] for e in errors), (
        errors
    )

    # 5. Unknown key under additionalProperties: false
    system = _load_template("mixed-bus-mock")
    system["topology"]["providers"]["bread0"]["config"]["require_live_session"] = True
    errors = projects.validate_system_payload(system)
    assert any(e["code"] == "provider.schema" and "bread0" in e["path"] for e in errors), errors


def test_validate_unique_annotations_catch_normalized_duplicates() -> None:
    """10 vs '0x0A' name the same address — uniqueItems misses it, x-anolis-unique must not."""
    system = _load_template("mixed-bus-mock")
    bread = system["topology"]["providers"]["bread0"]["config"]
    bread["devices"][1]["address"] = 10  # rlht0 already claims "0x0A"
    bread["discovery"]["addresses"] = ["0x0A", 10, "0x15"]
    errors = projects.validate_system_payload(system)
    unique_errors = [e for e in errors if e["code"] == "provider.unique"]
    paths = {e["path"] for e in unique_errors}
    assert "$.topology.providers.bread0.config.devices" in paths, errors
    assert "$.topology.providers.bread0.config.discovery.addresses" in paths, errors


def test_validate_unique_annotations_catch_duplicate_device_ids() -> None:
    system = _load_template("sim-quickstart")
    devices = system["topology"]["providers"]["sim0"]["config"]["devices"]
    devices[1]["id"] = devices[0]["id"]
    errors = projects.validate_system_payload(system)
    assert any(
        e["code"] == "provider.unique" and e["path"] == "$.topology.providers.sim0.config.devices" for e in errors
    ), errors


def test_save_project_writes_outputs_for_valid_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", tmp_path / "systems")
    system = _load_template("sim-quickstart")

    projects.save_project("valid-project", system)

    project_root = tmp_path / "systems" / "valid-project"
    assert (project_root / "system.json").is_file()
    assert (project_root / "anolis-runtime.yaml").is_file()
    assert (project_root / "providers" / "sim0.yaml").is_file()
