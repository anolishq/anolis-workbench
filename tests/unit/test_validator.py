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
    """A frozen system.json template.

    validator.py speaks the retired document (#255 moved cross-provider checks
    into canonical_validator.py); it survives only until the legacy
    save path is deleted, so its tests read the frozen fixtures.
    """
    tpl_path = pathlib.Path(__file__).parent.parent / "fixtures" / "v2-templates" / f"{name}.json"
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
