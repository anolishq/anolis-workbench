"""Unit tests for the canonical authoring core (#255, PR 1).

These modules are additive and not yet wired into CRUD; what they must
guarantee up front is that anything the workbench AUTHORS satisfies the
contracts `install.sh` enforces at deploy time. The regexes and rules asserted
here are copied verbatim from `anolis/tools/install.sh` — if they drift, these
tests are the thing that notices.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

from anolis_workbench.core import canonical, canonical_validator, machine_profile

# Copied verbatim from anolis/tools/install.sh's render pass. Do NOT relax these
# to match the code — they are the external contract.
INSTALL_SH_PCMD = re.compile(r"\.\./anolis-provider-([a-z0-9-]+)/build/[^/]+/anolis-provider-([a-z0-9-]+)")
INSTALL_SH_PPATH = re.compile(r"\.\./anolis-projects/projects/([a-z0-9-]+)/(config|behaviors)/([^\s\"']+)")
# machine-profile.schema.json's machine_id pattern.
MACHINE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _runtime_doc(machine_id: str = "rig-a", *, automation: dict | None = None) -> dict:
    doc = {
        "runtime": {"name": machine_id},
        "http": {"enabled": True, "bind": "127.0.0.1", "port": 8080},
        "providers": [
            {
                "id": "bread0",
                "command": canonical.provider_command_token("bread"),
                "args": [
                    "--config",
                    canonical.project_path_token(machine_id, canonical.provider_config_relpath("bread", "bread0")),
                ],
                "timeout_ms": 5000,
            }
        ],
        "polling": {"interval_ms": 500},
        "logging": {"level": "info"},
    }
    if automation is not None:
        doc["automation"] = automation
    return doc


def _document(machine_id: str = "rig-a", *, variants: dict | None = None, components: dict | None = None) -> dict:
    return {
        "format": "machine-profile",
        "authored": True,
        "meta": {"name": "rig-a"},
        "profile": canonical.build_profile(
            machine_id=machine_id,
            display_name="Rig A",
            providers={"bread0": canonical.provider_config_relpath("bread", "bread0")},
            components=components
            or {
                "runtime": {"repo": "anolishq/anolis", "version": "0.1.39"},
                "providers": {"bread": {"repo": "anolishq/anolis-provider-bread", "version": "0.3.8"}},
            },
        ),
        "variants": variants if variants is not None else {canonical.MANUAL_VARIANT: _runtime_doc(machine_id)},
        "providers": {
            "bread0": {
                "kind": "bread",
                "config": {
                    "hardware": {"bus_path": "/dev/i2c-1"},
                    "discovery": {"mode": "manual", "addresses": ["0x0A"]},
                    "devices": [{"id": "rlht0", "type": "rlht", "address": "0x0A"}],
                },
            }
        },
        "host_paths": {"runtime_executable": "", "providers": {}},
    }


# ---------------------------------------------------------------------------
# Identity + deploy tokens (the install.sh contract)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("bioreactor-v1", "bioreactor-v1"),
        ("My_Rig 01", "my-rig-01"),
        ("UPPER", "upper"),
        ("__weird__", "weird"),
        ("!!!", "machine"),
    ],
)
def test_machine_id_satisfies_both_contracts(name: str, expected: str) -> None:
    machine_id = canonical.machine_id_from_name(name)
    assert machine_id == expected
    # Must satisfy the profile schema AND install.sh's path token class.
    assert MACHINE_ID_RE.fullmatch(machine_id), machine_id
    assert re.fullmatch(r"[a-z0-9-]+", machine_id), machine_id


def test_provider_command_token_matches_install_sh_pcmd() -> None:
    token = canonical.provider_command_token("bread")
    match = INSTALL_SH_PCMD.fullmatch(token)
    assert match is not None, token
    # install.sh rewrites using group(2) — the trailing kind must be right.
    assert match.group(2) == "bread"


def test_project_path_token_matches_install_sh_ppath() -> None:
    token = canonical.project_path_token("rig-a", canonical.provider_config_relpath("bread", "bread0"))
    match = INSTALL_SH_PPATH.fullmatch(token)
    assert match is not None, token
    assert match.group(1) == "rig-a"
    assert match.group(2) == "config"


def test_provider_config_filename_resolves_kind_for_install_sh() -> None:
    """install.sh takes the stem up to the FIRST dot as the provider kind."""
    name = canonical.provider_config_filename("bread", "bread0")
    assert name == "provider-bread.bread0.yaml"
    stem = pathlib.PurePosixPath(name).stem  # provider-bread.bread0
    assert stem.removeprefix("provider-").split(".")[0] == "bread"


def test_assert_deploy_tokens_accepts_authored_and_rejects_host_paths() -> None:
    doc = _runtime_doc("rig-a")
    assert canonical.assert_deploy_tokens("rig-a", doc) == []

    # A host path (what the OLD composer wrote) must be rejected — on Windows it
    # would not match pcmd at all, so install.sh would ship it unrewritten.
    doc["providers"][0]["command"] = "/opt/anolis/bin/anolis-provider-bread"
    problems = canonical.assert_deploy_tokens("rig-a", doc)
    assert any("deploy token" in p for p in problems), problems


def test_assert_deploy_tokens_catches_machine_id_mismatch() -> None:
    doc = _runtime_doc("other-rig")
    problems = canonical.assert_deploy_tokens("rig-a", doc)
    assert any("other-rig" in p and "rig-a" in p for p in problems), problems


# ---------------------------------------------------------------------------
# Inertness + runtime-config validation
# ---------------------------------------------------------------------------


def test_inertness_mirrors_install_sh() -> None:
    assert canonical.inertness_violation(_runtime_doc()) is None
    assert canonical.inertness_violation(_runtime_doc(automation={"enabled": False})) is None
    assert "enabled" in (canonical.inertness_violation(_runtime_doc(automation={"enabled": True})) or "")
    # Hooks present is a violation even when disabled — install.sh checks both.
    violation = canonical.inertness_violation(_runtime_doc(automation={"enabled": False, "mode_transition_hooks": []}))
    assert "mode_transition_hooks" in (violation or "")


def test_runtime_config_errors_flags_a_broken_doc() -> None:
    assert canonical.runtime_config_errors(_runtime_doc()) == []
    assert canonical.runtime_config_errors({}) != []  # providers is required


# ---------------------------------------------------------------------------
# Profile building
# ---------------------------------------------------------------------------


def test_build_profile_never_invents_pins() -> None:
    profile = canonical.build_profile(
        machine_id="rig-a",
        display_name="Rig A",
        providers={"bread0": canonical.provider_config_relpath("bread", "bread0")},
    )
    assert "components" not in profile  # pins are AUTHORED data
    assert profile["runtime_profiles"] == {canonical.MANUAL_VARIANT: "config/anolis-runtime.manual.yaml"}
    assert profile["providers"] == {"bread0": {"config": "config/provider-bread.bread0.yaml"}}
    assert machine_profile.schema_errors(profile) == []


def test_build_profile_with_variants_and_safety_is_schema_valid() -> None:
    profile = canonical.build_profile(
        machine_id="rig-a",
        display_name="Rig A",
        providers={"bread0": canonical.provider_config_relpath("bread", "bread0")},
        variants={
            canonical.MANUAL_VARIANT: canonical.variant_relpath(canonical.MANUAL_VARIANT),
            canonical.AUTOMATION_VARIANT: canonical.variant_relpath(canonical.AUTOMATION_VARIANT),
        },
        behaviors=["behaviors/main.xml"],
        safety={"estop_topology": "power_cut"},
        components={
            "runtime": {"repo": "anolishq/anolis", "version": "0.1.39"},
            "providers": {"bread": {"repo": "anolishq/anolis-provider-bread", "version": "0.3.8"}},
        },
    )
    assert machine_profile.schema_errors(profile) == []
    assert profile["safety"] == {"estop_topology": "power_cut"}
    assert canonical.pinned_kinds(profile) == {"bread"}


# ---------------------------------------------------------------------------
# Round-trip: read/write a canonical project
# ---------------------------------------------------------------------------


def test_write_then_read_round_trips(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    doc = _document()
    canonical.write_project(pdir, doc)
    canonical.write_sidecar(
        pdir,
        canonical.default_sidecar(name="rig-a", created="2026-01-01T00:00:00Z", machine_id="rig-a"),
    )

    assert (pdir / "machine-profile.yaml").is_file()
    assert (pdir / "config" / "anolis-runtime.manual.yaml").is_file()
    assert (pdir / "config" / "provider-bread.bread0.yaml").is_file()

    read = canonical.read_project(pdir)
    assert read["authored"] is True
    assert read["profile"] == doc["profile"]
    assert read["variants"] == doc["variants"]
    assert read["providers"]["bread0"]["config"] == doc["providers"]["bread0"]["config"]
    assert read["providers"]["bread0"]["kind"] == "bread"  # derived from components x filename


def test_save_is_idempotent(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    first = {p.name: p.read_bytes() for p in sorted((pdir / "config").iterdir())}
    first["machine-profile.yaml"] = (pdir / "machine-profile.yaml").read_bytes()

    canonical.write_project(pdir, canonical.read_project(pdir))
    second = {p.name: p.read_bytes() for p in sorted((pdir / "config").iterdir())}
    second["machine-profile.yaml"] = (pdir / "machine-profile.yaml").read_bytes()
    assert first == second


def test_unknown_keys_survive_a_round_trip(tmp_path: pathlib.Path) -> None:
    """The whole point of retiring system.json: the workbench holds the PARSED
    canonical documents, so keys it has no model for cannot be dropped."""
    pdir = tmp_path / "rig-a"
    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT]["runtime"]["data_dir"] = "/var/lib/anolis"
    doc["variants"][canonical.MANUAL_VARIANT]["http"]["thread_pool_size"] = 8
    doc["variants"][canonical.MANUAL_VARIANT]["automation"] = {
        "enabled": False,
        "manual_gating_policy": "require_ack",
        "parameters": {"setpoint_c": {"min": 20, "max": 40}},
    }
    canonical.write_project(pdir, doc)

    manual = canonical.read_project(pdir)["variants"][canonical.MANUAL_VARIANT]
    assert manual["runtime"]["data_dir"] == "/var/lib/anolis"
    assert manual["http"]["thread_pool_size"] == 8
    assert manual["automation"]["manual_gating_policy"] == "require_ack"
    assert manual["automation"]["parameters"]["setpoint_c"] == {"min": 20, "max": 40}


def test_bind_is_emitted_unquoted_for_the_install_sh_lan_rewrite(tmp_path: pathlib.Path) -> None:
    """install.sh's LAN-exposure rewrite matches `^\\s*bind:\\s*127\\.0\\.0\\.1\\s*$`.
    If a serializer change ever quotes it, the bind+auth_enabled rewrite
    silently stops firing and the runtime refuses to start on a LAN bind."""
    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    text = (pdir / "config" / "anolis-runtime.manual.yaml").read_text(encoding="utf-8")
    assert re.search(r"^\s*bind:\s*127\.0\.0\.1\s*$", text, re.MULTILINE), text


def test_hex_addresses_round_trip_as_quoted_strings(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    config = yaml.safe_load((pdir / "config" / "provider-bread.bread0.yaml").read_text(encoding="utf-8"))
    assert config["devices"][0]["address"] == "0x0A"
    assert config["discovery"]["addresses"] == ["0x0A"]


def test_write_prunes_configs_the_profile_no_longer_references(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    stale = pdir / "config" / "provider-ezo.ezo0.yaml"
    stale.write_text("stale: true\n", encoding="utf-8")

    canonical.write_project(pdir, canonical.read_project(pdir))
    assert not stale.exists()
    assert (pdir / "config" / "provider-bread.bread0.yaml").is_file()


def test_sidecar_authored_flag_defaults_false_for_pre_255_sidecars(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    pdir.mkdir()
    # A #274-era sidecar has no `authored` key — it is an import.
    (pdir / machine_profile.SIDECAR_NAME).write_text(
        json.dumps({"schema_version": 1, "format": "machine-profile", "meta": {}}), encoding="utf-8"
    )
    assert canonical.is_authored(machine_profile.read_sidecar(pdir)) is False

    canonical.write_sidecar(pdir, canonical.default_sidecar(name="a", created="t", machine_id="rig-a"))
    assert canonical.is_authored(machine_profile.read_sidecar(pdir)) is True


# ---------------------------------------------------------------------------
# Validator — the three install.sh gate mirrors + coherence
# ---------------------------------------------------------------------------


def test_valid_document_passes() -> None:
    assert canonical_validator.validate_project(_document()) == []


def test_gate_manual_variant_must_be_inert() -> None:
    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT]["automation"] = {"enabled": True, "behavior_tree": "b.xml"}
    errors = canonical_validator.validate_project(doc)
    assert any("not inert" in e and canonical.AUTOMATION_VARIANT in e for e in errors), errors


def test_gate_provider_kind_must_be_pinned() -> None:
    doc = _document()
    doc["providers"]["bread0"]["kind"] = "ezo"  # not in components.providers
    errors = canonical_validator.validate_project(doc)
    assert any("not in the profile's pinned components" in e for e in errors), errors


def test_gate_path_tokens_must_carry_the_machine_id() -> None:
    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT] = _runtime_doc("someone-else")
    errors = canonical_validator.validate_project(doc)
    assert any("someone-else" in e for e in errors), errors


def test_missing_manual_variant_is_rejected() -> None:
    doc = _document(variants={})
    doc["profile"]["runtime_profiles"] = {canonical.AUTOMATION_VARIANT: "config/anolis-runtime.automation.yaml"}
    errors = canonical_validator.validate_project(doc)
    assert any("manual" in e for e in errors), errors


def test_provider_coherence_across_profile_variant_and_config() -> None:
    doc = _document()
    doc["providers"]["ezo0"] = {"kind": "ezo", "config": {}}
    errors = canonical_validator.validate_project(doc)
    assert any("not declared in the machine-profile" in e and "ezo0" in e for e in errors), errors

    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT]["providers"] = []
    errors = canonical_validator.validate_project(doc)
    assert any("never run by variant" in e for e in errors), errors


def test_cross_provider_i2c_conflict_is_capability_driven() -> None:
    doc = _document()
    doc["profile"]["providers"]["ezo0"] = {"config": canonical.provider_config_relpath("ezo", "ezo0")}
    doc["profile"]["components"]["providers"]["ezo"] = {"repo": "anolishq/anolis-provider-ezo", "version": "0.3.4"}
    doc["providers"]["ezo0"] = {
        "kind": "ezo",
        "config": {
            "hardware": {"bus_path": "/dev/i2c-1"},
            "devices": [{"id": "ph0", "type": "ph", "address": 10}],  # decimal 10 == 0x0A
        },
    }
    doc["variants"][canonical.MANUAL_VARIANT]["providers"].append(
        {
            "id": "ezo0",
            "command": canonical.provider_command_token("ezo"),
            "args": [
                "--config",
                canonical.project_path_token("rig-a", canonical.provider_config_relpath("ezo", "ezo0")),
            ],
        }
    )
    errors = canonical_validator.validate_project(doc)
    assert any("0x0A" in e and "claimed by both" in e for e in errors), errors


def test_workbench_port_conflict_is_reported() -> None:
    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT]["http"]["port"] = 3010
    errors = canonical_validator.validate_project(doc)
    assert any("3010" in e for e in errors), errors


def test_unknown_kind_errors_uses_the_vendored_envelopes() -> None:
    assert canonical_validator.unknown_kind_errors({"bread0": {"kind": "bread"}}) == []
    assert canonical_validator.unknown_kind_errors({"x0": {"kind": "nope"}}) != []
    assert canonical_validator.unknown_kind_errors({"x0": {"kind": None}}) != []


def test_path_token_warning_on_dir_name_mismatch() -> None:
    profile = {"machine_id": "rig-a"}
    assert canonical_validator.path_token_warnings(profile, "rig-a") == []
    assert canonical_validator.path_token_warnings(profile, "other") != []
