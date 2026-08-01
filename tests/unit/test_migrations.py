"""Unit tests for core/migrations.py — system.json v1 -> v2 (#270).

The v1 fixtures under tests/fixtures/v1-templates/ are the pre-#270 bundled
templates, frozen verbatim. The strongest correctness anchor is that every
migrated document validates against the providers' own vendored
--config-schema envelopes (the provider is the authority on its config shape).
"""

import copy
import json
import pathlib

import pytest
import yaml

from anolis_workbench.core import migrations, projects, renderer

V1_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "v1-templates"
TEMPLATES = pathlib.Path(__file__).parent.parent.parent / "anolis_workbench" / "templates"

TEMPLATE_NAMES = ("sim-quickstart", "bioreactor-manual", "mixed-bus-mock")


def _load_v1(name: str) -> dict:
    return json.loads((V1_FIXTURES / f"{name}.v1.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _load_v2_template(name: str) -> dict:
    return json.loads((TEMPLATES / name / "system.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_v1_template_migrates_to_checked_in_v2_template(name: str) -> None:
    migrated, changed = migrations.migrate_system(_load_v1(name))
    assert changed
    assert migrated == _load_v2_template(name)


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_migrated_document_passes_full_validation(name: str) -> None:
    migrated, _ = migrations.migrate_system(_load_v1(name))
    assert projects.validate_system_payload(migrated) == []


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_migration_is_idempotent(name: str) -> None:
    migrated, _ = migrations.migrate_system(_load_v1(name))
    again, changed = migrations.migrate_system(copy.deepcopy(migrated))
    assert not changed
    assert again == migrated


def test_migration_does_not_mutate_input() -> None:
    v1 = _load_v1("bioreactor-manual")
    snapshot = copy.deepcopy(v1)
    migrations.migrate_system(v1)
    assert v1 == snapshot


def test_bus_path_moves_from_paths_into_config() -> None:
    migrated, _ = migrations.migrate_system(_load_v1("bioreactor-manual"))
    for pid in ("bread0", "ezo0"):
        assert "bus_path" not in migrated["paths"]["providers"][pid]
        assert migrated["topology"]["providers"][pid]["config"]["hardware"]["bus_path"] == "/dev/i2c-1"


def test_addresses_keep_their_authored_hex_string_form() -> None:
    migrated, _ = migrations.migrate_system(_load_v1("bioreactor-manual"))
    bread = migrated["topology"]["providers"]["bread0"]["config"]
    assert bread["discovery"]["addresses"] == ["0x0A", "0x14", "0x15"]
    assert [d["address"] for d in bread["devices"]] == ["0x0A", "0x14", "0x15"]


def test_render_parity_with_old_renderer_output() -> None:
    """Migrated-then-rendered configs must match what the old per-kind renderer
    emitted for the same v1 document, modulo the address representation
    (old: ints; now: the authored hex strings, which providers parse)."""
    migrated, _ = migrations.migrate_system(_load_v1("bioreactor-manual"))
    outputs = renderer.render(migrated, "bioreactor-manual")

    bread = yaml.safe_load(outputs["providers/bread0.yaml"])
    assert bread["provider"] == {"name": "bread0"}
    assert bread["hardware"] == {
        "bus_path": "/dev/i2c-1",
        "query_delay_us": 15000,
        "timeout_ms": 150,
        "retry_count": 3,
    }
    # Old renderer emitted these as ints; the values must be numerically identical.
    assert [int(str(a), 0) for a in bread["discovery"]["addresses"]] == [10, 20, 21]
    assert bread["discovery"]["mode"] == "manual"
    assert [int(str(d["address"]), 0) for d in bread["devices"]] == [10, 20, 21]
    assert [d["id"] for d in bread["devices"]] == ["rlht0", "dcmt0", "dcmt1"]

    ezo = yaml.safe_load(outputs["providers/ezo0.yaml"])
    assert ezo["discovery"] == {"mode": "manual"}
    assert [int(str(d["address"]), 0) for d in ezo["devices"]] == [0x63, 0x61]

    sim_migrated, _ = migrations.migrate_system(_load_v1("sim-quickstart"))
    sim = yaml.safe_load(renderer.render(sim_migrated, "sim-quickstart")["providers/sim0.yaml"])
    assert sim["provider"] == {"name": "sim0"}
    assert sim["startup_policy"] == "degraded"
    assert sim["simulation"] == {"mode": "non_interacting", "tick_rate_hz": 10.0}
    assert sim["devices"][0] == {"id": "tempctl0", "type": "tempctl", "initial_temp": 25.0}
    assert sim["devices"][1] == {"id": "motorctl0", "type": "motorctl", "max_speed": 3000.0}


def test_sim_inert_mode_drops_tick_rate() -> None:
    v1 = _load_v1("sim-quickstart")
    v1["topology"]["providers"]["sim0"]["simulation_mode"] = "inert"
    migrated, _ = migrations.migrate_system(v1)
    assert migrated["topology"]["providers"]["sim0"]["config"]["simulation"] == {"mode": "inert"}


def test_get_project_migrates_and_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    systems_root = tmp_path / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", systems_root)
    project_dir = systems_root / "legacy"
    project_dir.mkdir(parents=True)
    (project_dir / "system.json").write_text(json.dumps(_load_v1("mixed-bus-mock")), encoding="utf-8")

    system = projects.get_project("legacy")
    assert system["schema_version"] == 2
    assert "config" in system["topology"]["providers"]["bread0"]

    on_disk = json.loads((project_dir / "system.json").read_text(encoding="utf-8"))
    assert on_disk == system  # persisted, not just served


def test_unknown_schema_version_left_untouched() -> None:
    doc = {"schema_version": 3, "meta": {}}
    out, changed = migrations.migrate_system(doc)
    assert not changed
    assert out is doc
