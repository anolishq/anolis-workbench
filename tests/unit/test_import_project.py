"""Unit tests for #226 — near-passthrough import of machine-profile projects.

The load-bearing property is VERBATIM-NESS: every imported file must be
byte-identical at every stage (source -> workspace -> materialized deploy
dir), including safety.estop_topology, all runtime_profiles variants, YAML
comments, and the automation variant's full safety contract.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil

import pytest

from anolis_workbench.core import deploy, machine_profile, projects

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "imported-profile"

COPIED = ("machine-profile.yaml", "config", "behaviors")


def _tree_digests(root: pathlib.Path, *, exclude: tuple[str, ...] = ()) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel in exclude:
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture()
def systems_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)
    return root


@pytest.fixture()
def source_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A pristine copy of the fixture so tests can mutate it safely."""
    dest = tmp_path / "imported-profile"
    shutil.copytree(FIXTURE, dest)
    return dest


# ---------------------------------------------------------------------------
# import_project
# ---------------------------------------------------------------------------


def test_import_copies_byte_identical(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    meta, warnings = projects.import_project(str(source_dir), "rig-a")

    assert meta["profile_dir_name"] == "imported-profile"
    assert warnings == []
    imported = systems_root / "rig-a"
    assert _tree_digests(imported, exclude=(projects.SIDECAR_NAME,)) == _tree_digests(source_dir)

    sidecar = json.loads((imported / projects.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert sidecar["format"] == projects.FORMAT_MACHINE_PROFILE
    assert sidecar["meta"]["imported_from"] == str(source_dir.resolve())


def test_import_rejects_existing_name(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")
    with pytest.raises(ValueError, match="already exists"):
        projects.import_project(str(source_dir), "rig-a")


def test_import_hard_fails_on_missing_referenced_file(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    (source_dir / "config" / "provider-ezo.bioreactor.yaml").unlink()
    with pytest.raises(projects.ImportValidationError) as exc_info:
        projects.import_project(str(source_dir), "rig-a")
    assert any("provider-ezo" in e for e in exc_info.value.errors)
    assert not (systems_root / "rig-a").exists()  # nothing half-copied


def test_import_hard_fails_on_schema_invalid_profile(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    profile_path = source_dir / "machine-profile.yaml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace("machine_id: imported-profile\n", ""),
        encoding="utf-8",
    )
    with pytest.raises(projects.ImportValidationError) as exc_info:
        projects.import_project(str(source_dir), "rig-a")
    assert any("machine_id" in e for e in exc_info.value.errors)


def test_import_warns_on_mismatched_project_dir_name(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    renamed = tmp_path / "other-name"
    source_dir.rename(renamed)
    _, warnings = projects.import_project(str(renamed), "rig-a")
    assert any("other-name" in w and "path rewrites" in w for w in warnings), warnings


def test_import_warns_on_non_inert_manual_variant(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    manual = source_dir / "config" / "anolis-runtime.bioreactor.manual.yaml"
    manual.write_text(
        manual.read_text(encoding="utf-8").replace("automation:\n  enabled: false", "automation:\n  enabled: true"),
        encoding="utf-8",
    )
    _, warnings = projects.import_project(str(source_dir), "rig-a")
    assert any("verify-inert" in w for w in warnings), warnings


# ---------------------------------------------------------------------------
# CRUD behavior on imported projects
# ---------------------------------------------------------------------------


def test_imported_project_listing_and_get(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")

    listing = projects.list_projects()
    assert [(p["name"], p["format"]) for p in listing] == [("rig-a", "machine-profile")]

    doc = projects.get_project("rig-a")
    assert doc["format"] == "machine-profile"
    assert doc["profile"]["safety"] == {"estop_topology": "power_cut"}
    assert sorted(doc["profile"]["runtime_profiles"]) == ["automation", "full", "manual", "telemetry"]
    # get_project must not have rewritten anything on disk
    assert _tree_digests(systems_root / "rig-a", exclude=(projects.SIDECAR_NAME,)) == _tree_digests(source_dir)


def test_save_rejected_for_imported(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")
    with pytest.raises(projects.ReadOnlyProjectError):
        projects.save_project("rig-a", {"schema_version": 2})


def test_duplicate_imported_is_verbatim(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")
    doc = projects.duplicate_project("rig-a", "rig-b")
    assert doc["format"] == "machine-profile"
    assert doc["meta"]["name"] == "rig-b"
    assert _tree_digests(systems_root / "rig-b", exclude=(projects.SIDECAR_NAME,)) == _tree_digests(source_dir)


# ---------------------------------------------------------------------------
# materialize_imported_project_dir
# ---------------------------------------------------------------------------


def test_materialize_is_byte_identical_and_offline(
    systems_root: pathlib.Path,
    source_dir: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects.import_project(str(source_dir), "rig-a")

    def _no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("materialize_imported_project_dir must not touch the network")

    monkeypatch.setattr(deploy.requests, "get", _no_network)
    monkeypatch.setattr(deploy.releases.requests, "get", _no_network)

    mat = deploy.materialize_imported_project_dir(systems_root / "rig-a", tmp_path / "out")

    # Keyed on the ORIGINAL canonical dir name, not the workbench name.
    assert mat.project_dir == tmp_path / "out" / "imported-profile"
    assert mat.runtime_version == "0.1.39"  # from components, verbatim — no release lookup
    assert mat.provider_kinds == {"bread0": "bread", "ezo0": "ezo"}
    assert _tree_digests(mat.project_dir) == _tree_digests(source_dir)


def test_materialize_hard_fails_without_components(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    profile_path = source_dir / "machine-profile.yaml"
    text = profile_path.read_text(encoding="utf-8")
    profile_path.write_text(text[: text.index("components:")], encoding="utf-8")
    projects.import_project(str(source_dir), "rig-a")

    with pytest.raises(deploy.DeployError, match="components"):
        deploy.materialize_imported_project_dir(projects.SYSTEMS_ROOT / "rig-a", tmp_path / "out")


def test_materialize_rejects_unknown_variant(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    projects.import_project(str(source_dir), "rig-a")
    with pytest.raises(deploy.DeployError, match="variant 'warp'"):
        deploy.materialize_imported_project_dir(projects.SYSTEMS_ROOT / "rig-a", tmp_path / "out", variant="warp")


def test_install_args_variant() -> None:
    args = deploy._install_args(
        "/tmp/p",
        prefix=deploy.DEFAULT_INSTALL_PREFIX,
        no_start=False,
        dry_run=True,
        variant="automation",
    )
    assert args == ["--project", "/tmp/p", "--variant", "automation", "--dry-run"]


# ---------------------------------------------------------------------------
# machine_profile helpers
# ---------------------------------------------------------------------------


def test_derive_kinds_from_components_and_filenames(source_dir: pathlib.Path) -> None:
    profile = machine_profile.load_profile(source_dir)
    assert machine_profile.derive_kinds(profile, source_dir) == {"bread0": "bread", "ezo0": "ezo"}


def test_derive_kinds_unknown_without_components(source_dir: pathlib.Path) -> None:
    profile = machine_profile.load_profile(source_dir)
    del profile["components"]
    assert machine_profile.derive_kinds(profile, source_dir) == {"bread0": None, "ezo0": None}


def test_validate_project_dir_reports_missing_dir(tmp_path: pathlib.Path) -> None:
    report = machine_profile.validate_project_dir(tmp_path / "nope", "nope")
    assert not report.ok
    assert any("Not a directory" in e for e in report.errors)
