"""provider remove: remove an imported schema, refusing while a project pins it (#786, #286).

Every case runs against a temporary import directory and the empty workspace
conftest gives each test; nothing touches the network.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from anolis_workbench.cli import main as cli_main
from anolis_workbench.core import canonical, machine_profile, projects, provider_import, provider_schemas


@pytest.fixture(autouse=True)
def _imports_dir(monkeypatch, tmp_path):
    directory = tmp_path / "data" / "schemas" / "providers"
    monkeypatch.setattr(provider_schemas.paths_module, "IMPORTED_PROVIDER_SCHEMAS_DIR", directory)
    provider_schemas._envelope_cache = None
    provider_schemas._reset_imports_cache()
    yield directory
    provider_schemas._envelope_cache = None
    provider_schemas._reset_imports_cache()


def _import(tmp_path: pathlib.Path, kind: str, version: str) -> None:
    doc = json.loads(json.dumps(provider_schemas._load_envelopes()["sim"]))
    doc["provider"] = f"anolis-provider-{kind}"
    doc["provider_version"] = version
    source = tmp_path / f"{kind}-{version}.json"
    source.write_text(json.dumps(doc), encoding="utf-8")
    provider_import.import_file(source)


def _project(name: str, *, uses: str, pins: dict[str, str]) -> pathlib.Path:
    """A project on disk whose one provider is of kind `uses`, pinning `pins`."""
    projects.create_project_from_template(name, "sim-quickstart")
    pdir = projects.project_dir(name)
    profile = machine_profile.load_profile(pdir)
    if uses != "sim":
        (pdir / "config" / "provider-sim.sim0.yaml").rename(pdir / "config" / f"provider-{uses}.sim0.yaml")
        profile["providers"]["sim0"]["config"] = f"config/provider-{uses}.sim0.yaml"
        runtime_path = pdir / profile["runtime_profiles"]["manual"]
        runtime = canonical._load_yaml(runtime_path)
        runtime["providers"][0]["command"] = canonical.provider_command_token(uses)
        canonical._write_yaml_atomic(runtime_path, runtime)
    profile["components"]["providers"] = {
        kind: {"repo": f"anolishq/anolis-provider-{kind}", "version": version} for kind, version in pins.items()
    }
    canonical._write_yaml_atomic(pdir / machine_profile.PROFILE_FILENAME, profile)
    return pdir


def _names(directory: pathlib.Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


# --- removal ---------------------------------------------------------------------


def test_remove_deletes_the_envelope_and_sidecar_and_a_warm_cache_sees_it(tmp_path, _imports_dir):
    _import(tmp_path, "foo", "0.1.0")
    # Warm every cache first, as a running server has.
    assert "foo" in provider_schemas.available_kinds()
    resolution = provider_schemas.resolve("foo", "0.1.0")
    assert resolution is not None and resolution.source == "sideloaded"

    result = provider_import.remove_import("foo@0.1.0")
    assert (result.kind, result.version) == ("foo", "0.1.0")
    assert result.pinned_by == []
    assert _names(_imports_dir) == []
    assert provider_schemas.resolve("foo", "0.1.0") is None
    assert "foo" not in provider_schemas.available_kinds()
    assert provider_import.list_imports() == []


def test_remove_leaves_other_versions_and_the_fallback_moves(tmp_path, _imports_dir):
    _import(tmp_path, "foo", "0.1.0")
    _import(tmp_path, "foo", "0.2.0")
    warm = provider_schemas.resolve("foo", "0.3.0")
    assert warm is not None and warm.envelope_version == "0.2.0"

    provider_import.remove_import("foo@0.2.0")
    assert _names(_imports_dir) == ["foo@0.1.0.config-schema.json", "foo@0.1.0.source.json"]
    fallback = provider_schemas.resolve("foo", "0.3.0")
    assert fallback is not None and fallback.envelope_version == "0.1.0"


def test_remove_refuses_while_a_project_pins_and_uses_it_naming_it(tmp_path, _imports_dir):
    _import(tmp_path, "foo", "0.1.0")
    _project("rig-a", uses="foo", pins={"foo": "0.1.0"})
    with pytest.raises(provider_import.RemoveRefused, match="rig-a") as refused:
        provider_import.remove_import("foo@0.1.0")
    assert "--force" in str(refused.value)
    assert _names(_imports_dir) == ["foo@0.1.0.config-schema.json", "foo@0.1.0.source.json"]
    assert provider_schemas.resolve("foo", "0.1.0") is not None


def test_remove_with_force_removes_and_reports_the_projects(tmp_path, _imports_dir):
    _import(tmp_path, "foo", "0.1.0")
    _project("rig-a", uses="foo", pins={"foo": "0.1.0"})
    result = provider_import.remove_import("foo@0.1.0", force=True)
    assert result.pinned_by == ["rig-a"]
    assert _names(_imports_dir) == []


def test_a_project_pinning_another_version_or_not_using_the_kind_does_not_block(tmp_path, _imports_dir):
    _import(tmp_path, "foo", "0.1.0")
    _project("other-version", uses="foo", pins={"foo": "0.2.0"})
    _project("pins-but-unused", uses="sim", pins={"sim": "0.2.8", "foo": "0.1.0"})
    result = provider_import.remove_import("foo@0.1.0")
    assert result.pinned_by == []
    assert _names(_imports_dir) == []


def test_a_project_that_cannot_be_read_blocks_unless_forced(tmp_path, _imports_dir):
    _import(tmp_path, "foo", "0.1.0")
    pdir = _project("broken", uses="foo", pins={"foo": "0.1.0"})
    (pdir / machine_profile.PROFILE_FILENAME).write_text("providers: [unclosed\n", encoding="utf-8")
    with pytest.raises(provider_import.RemoveRefused, match="broken"):
        provider_import.remove_import("foo@0.1.0")
    assert len(_names(_imports_dir)) == 2
    provider_import.remove_import("foo@0.1.0", force=True)
    assert _names(_imports_dir) == []


def test_an_interrupted_removal_leaves_no_import_a_reader_can_see(tmp_path, _imports_dir, monkeypatch):
    """The envelope goes first, so failing on the second file leaves only an
    orphan sidecar, never a live import missing its origin."""
    _import(tmp_path, "foo", "0.1.0")
    assert provider_schemas.resolve("foo", "0.1.0") is not None
    real_unlink = pathlib.Path.unlink
    calls: list[str] = []

    def unlink(self, missing_ok=False):
        if self.parent != _imports_dir:
            return real_unlink(self, missing_ok=missing_ok)
        calls.append(self.name)
        if len(calls) == 2:
            raise PermissionError(13, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    # Scoped, so the import directory the fixture patched stays patched.
    with monkeypatch.context() as patch:
        patch.setattr(pathlib.Path, "unlink", unlink)
        with pytest.raises(provider_import.RemoveRefused):
            provider_import.remove_import("foo@0.1.0")
    assert calls == ["foo@0.1.0.config-schema.json", "foo@0.1.0.source.json"]
    assert _names(_imports_dir) == ["foo@0.1.0.source.json"]
    assert provider_import.list_imports() == []
    assert provider_schemas.resolve("foo", "0.1.0") is None


def test_an_import_a_packaged_envelope_supersedes_is_removed_without_force(tmp_path, _imports_dir, monkeypatch):
    _import(tmp_path, "sim", "9.9.9")
    _project("rig-a", uses="sim", pins={"sim": "9.9.9"})
    packaged = dict(provider_schemas._load_envelopes())
    upgraded = json.loads(json.dumps(packaged["sim"]))
    upgraded["provider_version"] = "9.9.9"
    monkeypatch.setattr(provider_schemas, "_envelope_cache", {**packaged, "sim": upgraded})

    result = provider_import.remove_import("sim@9.9.9")
    assert result.pinned_by == []
    assert _names(_imports_dir) == []
    resolution = provider_schemas.resolve("sim", "9.9.9")
    assert resolution is not None and resolution.source == "packaged"


def test_removing_what_is_not_imported_is_refused(tmp_path, _imports_dir):
    _import(tmp_path, "foo", "0.1.0")
    with pytest.raises(provider_import.RemoveRefused, match="not imported"):
        provider_import.remove_import("foo@0.2.0")
    with pytest.raises(provider_import.RemoveRefused, match="not imported"):
        provider_import.remove_import("bar@0.1.0")
    assert len(_names(_imports_dir)) == 2


def test_a_packaged_version_cannot_be_removed(_imports_dir):
    version = provider_schemas._load_envelopes()["sim"]["provider_version"]
    with pytest.raises(provider_import.RemoveRefused, match="packaged"):
        provider_import.remove_import(f"sim@{version}")
    assert provider_schemas.resolve("sim", version) is not None


@pytest.mark.parametrize(
    "spec",
    [
        "../x@1.0.0",
        "foo@1.0.0/../../etc",
        "foo@../../1.0.0",
        "foo/../../x@1.0.0",
        "foo@1.0.0\n",
        "foo",
        "@1.0.0",
        "foo@",
        "Foo@1.0.0",
        "foo@1.0",
        "",
    ],
)
def test_a_malformed_argument_is_refused_and_nothing_is_touched(tmp_path, _imports_dir, spec):
    _import(tmp_path, "foo", "1.0.0")
    # Bait a traversal could reach: a file of the target's name outside the directory.
    outside = _imports_dir.parent / "x@1.0.0.config-schema.json"
    outside.write_text("{}", encoding="utf-8")
    before = {p: p.read_bytes() for p in _imports_dir.parent.rglob("*") if p.is_file()}
    with pytest.raises(provider_import.RemoveRefused):
        provider_import.remove_import(spec)
    assert {p: p.read_bytes() for p in _imports_dir.parent.rglob("*") if p.is_file()} == before


def test_the_target_must_be_directly_inside_the_import_directory(tmp_path, _imports_dir, monkeypatch):
    """The containment check holds even if the argument check were bypassed."""
    _import(tmp_path, "foo", "1.0.0")
    victim = _imports_dir.parent / "victim.config-schema.json"
    victim.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(provider_import, "stored_name", lambda kind, version: "../victim.config-schema.json")
    with pytest.raises(provider_import.RemoveRefused, match="outside"):
        provider_import.remove_import("foo@1.0.0", force=True)
    assert victim.exists()


# --- the save error once an import is gone --------------------------------------------


def test_saving_a_project_whose_pinned_import_is_gone_names_it_and_the_import_command(tmp_path):
    _import(tmp_path, "foo", "0.1.0")
    _project("rig-a", uses="foo", pins={"foo": "0.1.0"})
    projects.save_project("rig-a", projects.get_project("rig-a"))  # saves while imported

    provider_import.remove_import("foo@0.1.0", force=True)
    with pytest.raises(projects.ProjectValidationError) as refused:
        projects.save_project("rig-a", projects.get_project("rig-a"))
    (error,) = [e for e in refused.value.errors if e["code"] == "provider.unknown_kind"]
    assert error["source"] == "provider-schema"
    assert error["path"] == "$.providers.sim0.kind"
    assert "foo@0.1.0" in error["message"]
    assert "anolis-workbench provider import" in error["message"]


def test_the_unknown_kind_error_without_a_pin_is_unchanged():
    errors = projects._validate_canonical_provider_configs({"providers": {"x0": {"kind": "nope", "config": {}}}})
    (error,) = errors
    assert error["code"] == "provider.unknown_kind" and error["path"] == "$.providers.x0.kind"
    assert error["message"].startswith("Unknown provider kind 'nope' — no vendored config schema (known kinds: ")
    assert "provider import" not in error["message"]


# --- CLI -------------------------------------------------------------------------------


def test_cli_remove(tmp_path, _imports_dir, capsys):
    _import(tmp_path, "foo", "0.1.0")
    assert cli_main.main(["provider", "remove", "foo@0.1.0"]) == 0
    out = capsys.readouterr().out
    assert "foo@0.1.0" in out and "removed" in out
    assert _names(_imports_dir) == []


def test_cli_refuses_a_pinned_import_and_forces(tmp_path, _imports_dir, capsys):
    _import(tmp_path, "foo", "0.1.0")
    _project("rig-a", uses="foo", pins={"foo": "0.1.0"})
    assert cli_main.main(["provider", "remove", "foo@0.1.0"]) == 1
    captured = capsys.readouterr()
    assert "rig-a" in captured.err and "--force" in captured.err and captured.out == ""
    assert len(_names(_imports_dir)) == 2

    assert cli_main.main(["provider", "remove", "foo@0.1.0", "--force"]) == 0
    captured = capsys.readouterr()
    assert "removed" in captured.out and "rig-a" in captured.err
    assert _names(_imports_dir) == []


@pytest.mark.parametrize(
    ("spec", "reason"),
    [("foo@0.9.0", "not imported"), ("../x@1.0.0", "kind"), ("foo@1.0.0/../../etc", "semver")],
)
def test_cli_errors_exit_one(_imports_dir, capsys, spec, reason):
    assert cli_main.main(["provider", "remove", spec]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: not removed: ") and reason in err


def test_cli_packaged_exits_one(capsys):
    version = provider_schemas._load_envelopes()["sim"]["provider_version"]
    assert cli_main.main(["provider", "remove", f"sim@{version}"]) == 1
    assert "packaged" in capsys.readouterr().err


def test_the_scan_reads_projects_and_never_migrates_one(tmp_path, _imports_dir):
    """A legacy system.json project can't be read without migrating it, so it
    counts as can't-tell, and the scan leaves it exactly as it was."""
    _import(tmp_path, "foo", "0.1.0")
    legacy = projects.SYSTEMS_ROOT / "legacy"
    legacy.mkdir(parents=True)
    fixture = pathlib.Path(__file__).parent.parent / "fixtures" / "v2-templates" / "sim-quickstart.json"
    (legacy / "system.json").write_bytes(fixture.read_bytes())
    before = sorted(p.name for p in legacy.iterdir())

    with pytest.raises(provider_import.RemoveRefused, match="legacy"):
        provider_import.remove_import("foo@0.1.0")
    assert sorted(p.name for p in legacy.iterdir()) == before
    assert (legacy / "system.json").read_bytes() == fixture.read_bytes()
