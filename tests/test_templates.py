"""The bundled templates are shipped ARTIFACTS now (#255), not renderer input.

Before the flip a template was a system.json the workbench translated at deploy
time, so the tests here exercised the renderer. A template is now a canonical
project directory that gets copied to create a project and copied again to
deploy — which means every property install.sh depends on has to hold in the
checked-in files themselves. That is what this module asserts.
"""

from __future__ import annotations

import copy
import pathlib

import pytest
import yaml

from anolis_workbench.core import canonical, canonical_validator, machine_profile

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "anolis_workbench" / "templates"
FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "bioreactor"

TEMPLATES = ("sim-quickstart", "mixed-bus-mock", "bioreactor-manual")


def _load_yaml(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture(params=TEMPLATES)
def template(request: pytest.FixtureRequest) -> pathlib.Path:
    return TEMPLATES_DIR / str(request.param)


def test_every_bundled_template_is_shipped(template: pathlib.Path) -> None:
    assert template.is_dir()
    assert (template / machine_profile.PROFILE_FILENAME).is_file()
    assert (template / machine_profile.SIDECAR_NAME).is_file()


def test_template_is_a_valid_canonical_project(template: pathlib.Path) -> None:
    document = canonical.read_project(template)
    assert document["authored"] is True
    assert canonical_validator.validate_project(document) == []


def test_template_machine_id_matches_its_directory(template: pathlib.Path) -> None:
    """install.sh keys its `../anolis-projects/projects/<X>/` rewrites on the
    deploy directory basename, which is the machine_id — so a template whose id
    and directory disagree produces configs pointing at the wrong project."""
    profile = machine_profile.load_profile(template)
    assert profile["machine_id"] == template.name


def test_template_manual_variant_is_inert(template: pathlib.Path) -> None:
    """install.sh refuses a non-inert `manual` variant outright."""
    document = canonical.read_project(template)
    manual = document["variants"][canonical.MANUAL_VARIANT]
    assert canonical.inertness_violation(manual) is None


def test_template_paths_are_canonical_deploy_tokens(template: pathlib.Path) -> None:
    document = canonical.read_project(template)
    profile = document["profile"]
    kinds = {pid: entry["kind"] for pid, entry in document["providers"].items()}
    for variant, doc in document["variants"].items():
        problems = canonical.assert_deploy_tokens(profile["machine_id"], doc, kinds)
        assert problems == [], f"{template.name}/{variant}: {problems}"


def test_template_pins_every_kind_it_runs(template: pathlib.Path) -> None:
    """install.sh fetches provider binaries by `components.providers` KEY and
    hard-fails when a variant's command resolves to an unpinned kind."""
    document = canonical.read_project(template)
    pinned = canonical.pinned_kinds(document["profile"])
    assert pinned, f"{template.name} ships without component pins"
    for doc in document["variants"].values():
        for entry in doc["providers"]:
            assert canonical.command_kind(entry["command"]) in pinned


def test_template_provider_filenames_follow_the_install_sh_convention(template: pathlib.Path) -> None:
    """install.sh takes the provider config's stem up to the FIRST dot as the
    installed name, so the kind has to come first."""
    document = canonical.read_project(template)
    for pid, entry in document["profile"]["providers"].items():
        name = pathlib.PurePosixPath(entry["config"]).name
        assert name == canonical.provider_config_filename(document["providers"][pid]["kind"], pid)


def test_template_bind_is_emitted_unquoted(template: pathlib.Path) -> None:
    """install.sh's LAN-exposure rewrite matches `^\\s*bind: 127.0.0.1\\s*$`.
    A quoted value silently skips the rewrite AND the auth it turns on."""
    for rel in machine_profile.load_profile(template)["runtime_profiles"].values():
        text = (template / rel).read_text(encoding="utf-8")
        if "bind:" in text:
            assert "bind: 127.0.0.1\n" in text


def test_template_runtime_configs_are_schema_valid(template: pathlib.Path) -> None:
    for variant, doc in canonical.read_project(template)["variants"].items():
        assert canonical.runtime_config_errors(doc) == [], f"{template.name}/{variant}"


# ---------------------------------------------------------------------------
# Parity with the real bench project
# ---------------------------------------------------------------------------


def _normalize_runtime_doc(doc: dict) -> dict:
    """Everything except the path form, which is what the two layouts differ in:
    the baseline is an anolis-projects checkout, the template is a deploy-token
    project."""
    normalized = copy.deepcopy(doc)
    for provider in normalized.get("providers", []):
        provider.pop("command", None)
        provider.pop("args", None)
    return normalized


def test_bioreactor_template_still_matches_the_real_project_baselines() -> None:
    """The bundled template is the bench rig's config. If the two drift, a
    demo rig commissioned from the workbench stops matching the one that is
    actually known to work."""
    template = TEMPLATES_DIR / "bioreactor-manual"
    document = canonical.read_project(template)

    assert _normalize_runtime_doc(document["variants"][canonical.MANUAL_VARIANT]) == _normalize_runtime_doc(
        _load_yaml(FIXTURE_DIR / "anolis-runtime.bioreactor.manual.yaml")
    )
    assert document["providers"]["bread0"]["config"] == _load_yaml(FIXTURE_DIR / "provider-bread.bioreactor.yaml")
    assert document["providers"]["ezo0"]["config"] == _load_yaml(FIXTURE_DIR / "provider-ezo.bioreactor.yaml")


@pytest.mark.parametrize(
    "baseline",
    ["anolis-runtime.bioreactor.manual.yaml", "anolis-runtime.bioreactor.telemetry.yaml"],
)
def test_real_world_baselines_validate_against_the_vendored_schema(baseline: str) -> None:
    """The vendored runtime-config schema is what save-time validation rejects
    on — it must accept hand-authored configs that the runtime itself accepts."""
    assert canonical.runtime_config_errors(_load_yaml(FIXTURE_DIR / baseline)) == []
