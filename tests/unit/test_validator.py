"""Unit tests for backend/validator.py — cross-provider system validation.

Per-provider config correctness (required fields, duplicate device ids,
address ranges) is the provider schema's job since #270 — see
test_projects_validation.py. This module covers what only the workbench can
see across providers and against the runtime entry list.
"""

import json
import pathlib
import sys

from anolis_workbench.core import validator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_template(name: str) -> dict:
    tpl_path = pathlib.Path(__file__).parent.parent.parent / "anolis_workbench" / "templates" / name / "system.json"
    return json.loads(tpl_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _make_system(providers=None, runtime_port=8080, runtime_providers=None):
    """Build a minimal valid system dict for test mutation."""
    if providers is None:
        providers = {}
    if runtime_providers is None:
        runtime_providers = [{"id": pid} for pid in providers]
    return {
        "topology": {
            "runtime": {
                "http_port": runtime_port,
                "providers": runtime_providers,
            },
            "providers": providers,
        },
        "paths": {
            "runtime_executable": "../anolis/build/anolis-runtime",
            "providers": {pid: {} for pid in providers},
        },
    }


def _bus_provider(bus_path: str, devices: list) -> dict:
    """A provider entry whose native config declares an I2C bus + devices."""
    return {
        "kind": "bread",
        "config": {
            "hardware": {"bus_path": bus_path},
            "discovery": {"mode": "manual"},
            "devices": devices,
        },
    }


# ---------------------------------------------------------------------------
# Test: clean system produces no errors
# ---------------------------------------------------------------------------


def test_clean_sim_quickstart():
    system = _load_template("sim-quickstart")
    errors = validator.validate_system(system)
    assert errors == [], f"Expected no errors but got: {errors}"


def test_clean_mixed_bus_mock():
    system = _load_template("mixed-bus-mock")
    errors = validator.validate_system(system)
    assert errors == [], f"Expected no errors but got: {errors}"


def test_clean_bioreactor_manual_template():
    system = _load_template("bioreactor-manual")
    errors = validator.validate_system(system)
    assert errors == [], f"Expected no errors but got: {errors}"


def test_runtime_executable_required():
    system = _make_system(
        providers={"sim0": {"kind": "sim", "config": {}}},
        runtime_providers=[{"id": "sim0", "kind": "sim"}],
    )
    system["paths"]["runtime_executable"] = ""
    errors = validator.validate_system(system)
    assert any("Runtime executable path" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Test: duplicate provider IDs
# ---------------------------------------------------------------------------


def test_duplicate_provider_ids():
    system = _make_system(
        providers={"sim0": {"kind": "sim", "config": {}}},
        runtime_providers=[{"id": "sim0"}, {"id": "sim0"}],
    )
    errors = validator.validate_system(system)
    assert any("Duplicate provider IDs" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Test: reserved workbench port collision
# ---------------------------------------------------------------------------


def test_reserved_workbench_port_collision():
    system = _make_system(runtime_port=3010)
    errors = validator.validate_system(system)
    assert any("3010" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Test: cross-provider I2C address conflicts (capability-driven — any provider
# whose config carries hardware.bus_path + addressed devices participates)
# ---------------------------------------------------------------------------


def test_duplicate_i2c_address():
    system = _make_system(
        providers={
            "bread0": _bus_provider("/dev/i2c-1", [{"id": "dev0", "type": "rlht", "address": "0x62"}]),
            "ezo0": {
                "kind": "ezo",
                "config": {
                    "hardware": {"bus_path": "/dev/i2c-1"},
                    "discovery": {"mode": "manual"},
                    "devices": [{"id": "dev1", "type": "ph", "address": "0x62"}],
                },
            },
        },
        runtime_providers=[{"id": "bread0"}, {"id": "ezo0"}],
    )
    system["paths"]["providers"] = {
        "bread0": {"executable": "../anolis-provider-bread/build/bread"},
        "ezo0": {"executable": "../anolis-provider-ezo/build/ezo"},
    }
    errors = validator.validate_system(system)
    assert any("0x62" in e for e in errors), errors


def test_duplicate_i2c_address_mixed_literal_formats():
    """Decimal and hex literals for the same address must conflict on same bus."""
    system = _make_system(
        providers={
            "bread0": _bus_provider("/dev/i2c-1", [{"id": "dev0", "type": "rlht", "address": "20"}]),
            "ezo0": _bus_provider("/dev/i2c-1", [{"id": "dev1", "type": "ph", "address": "0x14"}]),
        },
        runtime_providers=[{"id": "bread0"}, {"id": "ezo0"}],
    )
    errors = validator.validate_system(system)
    assert any("0x14" in e for e in errors), errors


def test_same_address_different_bus_is_ok():
    """Same address on different bus paths must NOT produce an error."""
    system = _make_system(
        providers={
            "bread0": _bus_provider("/dev/i2c-1", [{"id": "dev0", "type": "rlht", "address": "0x62"}]),
            "ezo0": _bus_provider("/dev/i2c-2", [{"id": "dev1", "type": "ph", "address": "0x62"}]),
        },
        runtime_providers=[{"id": "bread0"}, {"id": "ezo0"}],
    )
    errors = validator.validate_system(system)
    address_errors = [e for e in errors if "0x62" in e]
    assert address_errors == [], address_errors


def test_same_provider_duplicate_address_left_to_provider_schema():
    """Within-provider duplicates are the schema's job (x-anolis-unique), not the cross-provider check."""
    system = _make_system(
        providers={
            "bread0": _bus_provider(
                "/dev/i2c-1",
                [
                    {"id": "dev0", "type": "rlht", "address": "0x62"},
                    {"id": "dev1", "type": "dcmt", "address": "0x62"},
                ],
            ),
        },
        runtime_providers=[{"id": "bread0"}],
    )
    errors = validator.validate_system(system)
    address_errors = [e for e in errors if "0x62" in e]
    assert address_errors == [], address_errors


def test_provider_without_bus_capability_is_ignored():
    """A provider whose config has no hardware.bus_path takes no part in bus checks."""
    system = _make_system(
        providers={
            "sim0": {"kind": "sim", "config": {"devices": [{"id": "d0", "type": "tempctl"}]}},
            "bread0": _bus_provider("/dev/i2c-1", [{"id": "dev0", "type": "rlht", "address": "0x62"}]),
        },
        runtime_providers=[{"id": "sim0"}, {"id": "bread0"}],
    )
    errors = validator.validate_system(system)
    assert errors == [] or all("0x62" not in e for e in errors), errors


# ---------------------------------------------------------------------------
# Test: provider in runtime list but no topology entry
# ---------------------------------------------------------------------------


def test_provider_in_runtime_missing_from_topology():
    system = _make_system(
        providers={},  # topology empty
        runtime_providers=[{"id": "ghost0"}],
    )
    errors = validator.validate_system(system)
    assert any("ghost0" in e and "runtime list" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Test: provider in topology but not in runtime list
# ---------------------------------------------------------------------------


def test_provider_in_topology_missing_from_runtime():
    system = _make_system(
        providers={"orphan0": {"kind": "sim", "config": {}}},
        runtime_providers=[],  # runtime list empty
    )
    errors = validator.validate_system(system)
    assert any("orphan0" in e and "runtime list" in e for e in errors), errors


def test_missing_executable_path():
    system = _make_system(
        providers={"sim0": {"kind": "sim", "config": {}}},
        runtime_providers=[{"id": "sim0", "kind": "sim"}],
    )
    system["paths"]["providers"]["sim0"] = {}
    errors = validator.validate_system(system)
    assert any("executable" in e for e in errors), errors


def test_restart_policy_requires_matching_backoff_length():
    system = _make_system(
        providers={"sim0": {"kind": "sim", "config": {}}},
        runtime_providers=[
            {
                "id": "sim0",
                "kind": "sim",
                "restart_policy": {
                    "enabled": True,
                    "max_attempts": 3,
                    "backoff_ms": [100, 200],
                    "timeout_ms": 30000,
                },
            }
        ],
    )
    errors = validator.validate_system(system)
    assert any("backoff_ms length" in e for e in errors), errors


def test_restart_policy_backoff_must_be_non_negative_ints():
    system = _load_template("mixed-bus-mock")
    restart_policy = system["topology"]["runtime"]["providers"][0]["restart_policy"]
    restart_policy["backoff_ms"] = [200, -1, 1000]
    errors = validator.validate_system(system)
    assert any("backoff_ms values" in e for e in errors), errors


def test_restart_policy_requires_timeout_greater_than_backoff():
    system = _load_template("mixed-bus-mock")
    restart_policy = system["topology"]["runtime"]["providers"][0]["restart_policy"]
    restart_policy["timeout_ms"] = 500
    errors = validator.validate_system(system)
    assert any("timeout_ms" in e for e in errors), errors


def test_automation_enabled_requires_behavior_tree_path():
    system = _make_system(
        providers={"sim0": {"kind": "sim", "config": {}}},
        runtime_providers=[{"id": "sim0", "kind": "sim"}],
    )
    system["topology"]["runtime"]["automation_enabled"] = True
    errors = validator.validate_system(system)
    assert any("Automation is enabled" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_clean_sim_quickstart,
        test_clean_mixed_bus_mock,
        test_clean_bioreactor_manual_template,
        test_runtime_executable_required,
        test_duplicate_provider_ids,
        test_reserved_workbench_port_collision,
        test_duplicate_i2c_address,
        test_duplicate_i2c_address_mixed_literal_formats,
        test_same_address_different_bus_is_ok,
        test_same_provider_duplicate_address_left_to_provider_schema,
        test_provider_without_bus_capability_is_ignored,
        test_provider_in_runtime_missing_from_topology,
        test_provider_in_topology_missing_from_runtime,
        test_missing_executable_path,
        test_restart_policy_requires_matching_backoff_length,
        test_restart_policy_backoff_must_be_non_negative_ints,
        test_restart_policy_requires_timeout_greater_than_backoff,
        test_automation_enabled_requires_behavior_tree_path,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
        except Exception as exc:
            print(f"  ERROR {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} tests passed.")
    sys.exit(0 if passed == len(tests) else 1)
