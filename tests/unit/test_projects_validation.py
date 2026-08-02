"""Save-time validation of a canonical project document (#255).

Two layers run on every save: `canonical_validator` (profile schema, runtime
schema, containment, and mirrors of the install.sh gates that would otherwise
only fail at `sudo` on the target) and the vendored provider config-schema
envelopes from #270.
"""

from __future__ import annotations

import copy
import pathlib

import pytest

from anolis_workbench.core import canonical, machine_profile, projects

TEMPLATES_ROOT = pathlib.Path(__file__).parent.parent.parent / "anolis_workbench" / "templates"


def _load_template(name: str) -> dict:
    """A bundled template as an in-memory canonical document."""
    document = canonical.read_project(TEMPLATES_ROOT / name)
    return copy.deepcopy(document)


def _manual(document: dict) -> dict:
    return document["variants"][canonical.MANUAL_VARIANT]  # type: ignore[no-any-return]


@pytest.fixture(autouse=True)
def _systems_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)
    return root


def test_save_project_rejects_schema_invalid_payload(_systems_root: pathlib.Path) -> None:
    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("invalid-schema", {"schema_version": 1})

    errors = exc_info.value.errors
    assert any(err.get("source") == "canonical" for err in errors), errors
    assert not (_systems_root / "invalid-schema" / machine_profile.PROFILE_FILENAME).exists()


def test_save_project_rejects_semantic_invalid_payload(_systems_root: pathlib.Path) -> None:
    document = _load_template("sim-quickstart")
    entry = copy.deepcopy(_manual(document)["providers"][0])
    _manual(document)["providers"].append(entry)  # same id twice

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("invalid-semantic", document)

    errors = exc_info.value.errors
    assert any("uplicate" in err.get("message", "") for err in errors), errors
    assert not (_systems_root / "invalid-semantic" / machine_profile.PROFILE_FILENAME).exists()


def test_save_project_rejects_unknown_provider_kind(_systems_root: pathlib.Path) -> None:
    """Kinds are the set of vendored config-schema envelopes; anything else is rejected."""
    document = _load_template("sim-quickstart")
    document["providers"]["custom0"] = {"kind": "custom", "config": {}}
    document["profile"]["providers"]["custom0"] = {"config": canonical.provider_config_relpath("custom", "custom0")}
    document["profile"]["compatibility"]["providers"]["custom0"] = {
        "strategy": "local-build",
        "version": "unspecified",
    }

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("invalid-custom", document)

    errors = exc_info.value.errors
    unknown = [err for err in errors if err.get("code") == "provider.unknown_kind"]
    assert unknown, errors
    assert unknown[0]["path"] == "$.providers.custom0.kind"
    assert "custom" in unknown[0]["message"]
    assert not (_systems_root / "invalid-custom" / machine_profile.PROFILE_FILENAME).exists()


def test_save_project_rejects_a_command_whose_kind_is_not_pinned(_systems_root: pathlib.Path) -> None:
    """install.sh iterates EVERY variant and sys.exits when a provider command
    does not resolve to a pinned component — mirror that at save time so it
    fails in the composer rather than at sudo on the target."""
    document = _load_template("sim-quickstart")
    _manual(document)["providers"][0]["command"] = canonical.provider_command_token("bread")

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("unpinned-kind", document)
    assert any("bread" in err.get("message", "") for err in exc_info.value.errors), exc_info.value.errors


def test_save_project_rejects_a_non_inert_manual_variant(_systems_root: pathlib.Path) -> None:
    """install.sh refuses to install a manual variant that boots into automation."""
    document = _load_template("sim-quickstart")
    _manual(document)["automation"] = {"enabled": True}

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("non-inert", document)
    assert any("inert" in err.get("message", "").lower() for err in exc_info.value.errors), exc_info.value.errors


def test_validate_provider_config_envelope_failure_matrix() -> None:
    """Representative envelope violations must surface as provider-schema errors
    with paths anchored under the provider's config."""
    document = _load_template("mixed-bus-mock")
    bread = document["providers"]["bread0"]["config"]

    # 1. Missing required section (hardware)
    del bread["hardware"]
    errors = projects.validate_project_payload(document)
    assert any(e["code"] == "provider.schema" and e["path"] == "$.providers.bread0.config" for e in errors), errors

    # 2. Wrong type deep in the config
    document = _load_template("mixed-bus-mock")
    document["providers"]["bread0"]["config"]["hardware"]["timeout_ms"] = "fast"
    errors = projects.validate_project_payload(document)
    assert any(
        e["code"] == "provider.schema" and e["path"] == "$.providers.bread0.config.hardware.timeout_ms" for e in errors
    ), errors

    # 3. Conditional: discovery.mode manual requires addresses
    document = _load_template("mixed-bus-mock")
    del document["providers"]["bread0"]["config"]["discovery"]["addresses"]
    errors = projects.validate_project_payload(document)
    assert any(
        e["code"] == "provider.schema" and e["path"].startswith("$.providers.bread0.config.discovery") for e in errors
    ), errors

    # 4. Out-of-range I2C address (pattern/anyOf)
    document = _load_template("mixed-bus-mock")
    document["providers"]["ezo0"]["config"]["devices"][0]["address"] = "0xFF"
    errors = projects.validate_project_payload(document)
    assert any(e["code"] == "provider.schema" and "devices[0]" in e["path"] and "ezo0" in e["path"] for e in errors), (
        errors
    )

    # 5. Unknown key under additionalProperties: false
    document = _load_template("mixed-bus-mock")
    document["providers"]["bread0"]["config"]["require_live_session"] = True
    errors = projects.validate_project_payload(document)
    assert any(e["code"] == "provider.schema" and "bread0" in e["path"] for e in errors), errors


def test_validate_unique_annotations_catch_normalized_duplicates() -> None:
    """10 vs '0x0A' name the same address — uniqueItems misses it, x-anolis-unique must not."""
    document = _load_template("mixed-bus-mock")
    bread = document["providers"]["bread0"]["config"]
    bread["devices"][1]["address"] = 10  # rlht0 already claims "0x0A"
    bread["discovery"]["addresses"] = ["0x0A", 10, "0x15"]

    errors = projects.validate_project_payload(document)
    paths = {e["path"] for e in errors if e["code"] == "provider.unique"}
    assert "$.providers.bread0.config.devices" in paths, errors
    assert "$.providers.bread0.config.discovery.addresses" in paths, errors


def test_validate_unique_annotations_catch_duplicate_device_ids() -> None:
    document = _load_template("sim-quickstart")
    devices = document["providers"]["sim0"]["config"]["devices"]
    devices[1]["id"] = devices[0]["id"]

    errors = projects.validate_project_payload(document)
    unique = [e for e in errors if e["code"] == "provider.unique"]
    assert any(e["path"] == "$.providers.sim0.config.devices" for e in unique), errors


def test_save_project_writes_the_canonical_layout(_systems_root: pathlib.Path) -> None:
    document = _load_template("sim-quickstart")
    projects.save_project("valid-project", document)

    root = _systems_root / "valid-project"
    assert (root / machine_profile.PROFILE_FILENAME).is_file()
    assert (root / canonical.variant_relpath(canonical.MANUAL_VARIANT)).is_file()
    assert (root / canonical.provider_config_relpath("sim", "sim0")).is_file()
    assert (root / machine_profile.SIDECAR_NAME).is_file()
    # No trace of the retired shadow document.
    assert not (root / "system.json").exists()
    assert not (root / "anolis-runtime.yaml").exists()

    reread = canonical.read_project(root)
    assert reread["authored"] is True
    assert reread["providers"]["sim0"]["config"] == document["providers"]["sim0"]["config"]


def test_save_project_rejects_an_off_host_bind_without_auth(_systems_root: pathlib.Path) -> None:
    """install.sh refuses this too, but only in its config phase — AFTER the
    binaries on the target have already been replaced. Catching it at save time
    keeps it a one-field fix instead of a half-finished install."""
    document = _load_template("sim-quickstart")
    _manual(document)["http"]["bind"] = "0.0.0.0"

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("open-bind", document)
    assert any("authentication" in err.get("message", "") for err in exc_info.value.errors)


def test_an_off_host_bind_with_auth_is_allowed(_systems_root: pathlib.Path) -> None:
    document = _load_template("sim-quickstart")
    _manual(document)["http"]["bind"] = "0.0.0.0"
    _manual(document)["http"]["auth_enabled"] = True

    projects.save_project("open-bind-auth", document)
    assert (_systems_root / "open-bind-auth" / machine_profile.PROFILE_FILENAME).is_file()


def test_loopback_binds_are_not_flagged(_systems_root: pathlib.Path) -> None:
    for bind in ("127.0.0.1", "127.0.1.1", "::1", "localhost"):
        document = _load_template("sim-quickstart")
        _manual(document)["http"]["bind"] = bind
        assert projects.validate_project_payload(document) == [], bind


def test_two_projects_cannot_share_a_deploy_directory(_systems_root: pathlib.Path) -> None:
    """install.sh `rm -rf`s {prefix}/projects/<dir> before installing, so a
    collision means deploying one project destroys the other's config on the
    rig. `Rig_A` and `rig-a` slugify identically, so this is easy to hit."""
    projects.create_project_from_template("Rig_A", "sim-quickstart")

    with pytest.raises(ValueError, match="already uses"):
        projects.create_project_from_template("rig-a", "sim-quickstart")
    with pytest.raises(ValueError, match="already uses"):
        projects.duplicate_project("Rig_A", "rig-a")


def test_rename_keeps_the_deploy_identity_and_does_not_self_collide(_systems_root: pathlib.Path) -> None:
    projects.create_project_from_template("Rig_A", "sim-quickstart")
    projects.rename_project("Rig_A", "Rig_A_old")
    # The deploy identity deliberately survives a rename...
    assert projects.get_project("Rig_A_old")["profile"]["machine_id"] == "rig-a"
    # ...which is exactly why the name it freed up must stay blocked.
    with pytest.raises(ValueError, match="already uses"):
        projects.create_project_from_template("rig-a", "sim-quickstart")
