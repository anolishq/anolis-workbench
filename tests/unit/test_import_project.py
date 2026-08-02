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


def _copied_subset(root: pathlib.Path) -> dict[str, str]:
    """Source digests restricted to what an import is supposed to copy."""
    return {
        rel: digest
        for rel, digest in _tree_digests(root).items()
        if rel == "machine-profile.yaml" or rel.startswith(("config/", "behaviors/"))
    }


def test_import_copies_byte_identical(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    meta, warnings = projects.import_project(str(source_dir), "rig-a")

    assert meta["profile_dir_name"] == "imported-profile"
    assert warnings == []
    imported = systems_root / "rig-a"
    assert _tree_digests(imported, exclude=(projects.SIDECAR_NAME,)) == _copied_subset(source_dir)

    sidecar = json.loads((imported / projects.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert sidecar["format"] == projects.FORMAT_MACHINE_PROFILE
    assert sidecar["meta"]["imported_from"] == str(source_dir.resolve())


def test_import_copies_only_the_declared_set(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    """docs/ (and anything else outside the copy set) stays in the source repo."""
    assert (source_dir / "docs" / "NOTES.md").is_file()  # fixture guard
    projects.import_project(str(source_dir), "rig-a")
    assert not (systems_root / "rig-a" / "docs").exists()


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
    assert _tree_digests(systems_root / "rig-a", exclude=(projects.SIDECAR_NAME,)) == _copied_subset(source_dir)


def test_save_rejected_for_imported(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")
    with pytest.raises(projects.ReadOnlyProjectError):
        projects.save_project("rig-a", {"schema_version": 2})


def test_duplicate_imported_is_verbatim(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")
    doc = projects.duplicate_project("rig-a", "rig-b")
    assert doc["format"] == "machine-profile"
    assert doc["meta"]["name"] == "rig-b"
    assert _tree_digests(systems_root / "rig-b", exclude=(projects.SIDECAR_NAME,)) == _copied_subset(source_dir)


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
    assert _tree_digests(mat.project_dir) == _copied_subset(source_dir)


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


# ---------------------------------------------------------------------------
# Reference containment (adversarial review: MAJOR 1 + MAJOR 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "expect"),
    [
        ("/etc/passwd", "absolute path"),
        ("../escape.yaml", "escapes the project directory"),
        ("config/../../escape.yaml", "escapes the project directory"),
    ],
)
def test_import_refuses_uncarriable_references(
    systems_root: pathlib.Path, source_dir: pathlib.Path, ref: str, expect: str
) -> None:
    """A reference the import cannot carry must HARD-FAIL, not produce a
    workspace silently missing the file (an absolute ref is the nasty case:
    Path(src) / "/etc/passwd" exists on the host, so a naive existence check
    passes at import AND at deploy while nothing is ever copied)."""
    profile_path = source_dir / "machine-profile.yaml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            "  manual: config/anolis-runtime.bioreactor.manual.yaml",
            f"  manual: {ref}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(projects.ImportValidationError) as exc_info:
        projects.import_project(str(source_dir), "rig-a")
    assert any(expect in e for e in exc_info.value.errors), exc_info.value.errors
    assert not (systems_root / "rig-a").exists()


def test_referenced_files_includes_validation_script() -> None:
    """validation.check_http_script is a profile-relative path too — it must be
    validated (and therefore carried), not silently dropped."""
    profile = {"validation": {"check_http_script": "config/check_http.sh"}}
    assert machine_profile.referenced_files(profile) == ["config/check_http.sh"]


def test_materialize_refuses_uncarriable_reference(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Defence in depth: even a hand-edited workspace cannot deploy a profile
    that references files outside the carried set."""
    projects.import_project(str(source_dir), "rig-a")
    profile_path = systems_root / "rig-a" / "machine-profile.yaml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            "  manual: config/anolis-runtime.bioreactor.manual.yaml",
            "  manual: /etc/passwd",
        ),
        encoding="utf-8",
    )
    with pytest.raises(deploy.DeployError, match="cannot carry"):
        deploy.materialize_imported_project_dir(systems_root / "rig-a", tmp_path / "out")


# ---------------------------------------------------------------------------
# Robustness (adversarial review MINORs)
# ---------------------------------------------------------------------------


def test_corrupt_sidecar_does_not_break_the_project_list(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    """A non-UTF-8 / unparseable sidecar must degrade to the profile-file
    fallback, not 500 the whole listing."""
    projects.import_project(str(source_dir), "rig-a")
    projects.sidecar_path("rig-a").write_bytes(b"\xff\xfe not json")

    listing = projects.list_projects()
    assert [(p["name"], p["format"]) for p in listing] == [("rig-a", "machine-profile")]
    assert projects.get_project("rig-a")["format"] == "machine-profile"


def test_materialize_rejects_traversal_in_sidecar_dir_name(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    projects.import_project(str(source_dir), "rig-a")
    sidecar_file = projects.sidecar_path("rig-a")
    sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
    sidecar["meta"]["profile_dir_name"] = "../escaped"
    sidecar_file.write_text(json.dumps(sidecar), encoding="utf-8")

    with pytest.raises(deploy.DeployError, match="invalid project directory name"):
        deploy.materialize_imported_project_dir(systems_root / "rig-a", tmp_path / "out")
    assert not (tmp_path / "escaped").exists()


def test_materialize_is_idempotent_into_the_same_dest(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    projects.import_project(str(source_dir), "rig-a")
    out = tmp_path / "out"
    first = deploy.materialize_imported_project_dir(systems_root / "rig-a", out)
    second = deploy.materialize_imported_project_dir(systems_root / "rig-a", out)
    assert _tree_digests(first.project_dir) == _tree_digests(second.project_dir)


def test_import_of_a_taken_name_leaves_the_existing_project_intact(
    systems_root: pathlib.Path, source_dir: pathlib.Path
) -> None:
    """The failure path must never rmtree a directory it did not create."""
    projects.import_project(str(source_dir), "rig-a")
    before = _tree_digests(systems_root / "rig-a")
    with pytest.raises(ValueError, match="already exists"):
        projects.import_project(str(source_dir), "rig-a")
    assert _tree_digests(systems_root / "rig-a") == before


def test_import_carries_referenced_dirs_outside_the_base_set(
    systems_root: pathlib.Path, source_dir: pathlib.Path
) -> None:
    """A profile may legally reference e.g. validation/check.sh; the copy set
    adapts so nothing the profile depends on is left behind (and the file is
    NOT copied merely because the directory exists — only when referenced)."""
    (source_dir / "validation").mkdir()
    (source_dir / "validation" / "check_http.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    # Not referenced yet -> not carried.
    projects.import_project(str(source_dir), "rig-plain")
    assert not (systems_root / "rig-plain" / "validation").exists()

    profile_path = source_dir / "machine-profile.yaml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            "behaviors:\n",
            "validation:\n  check_http_script: validation/check_http.sh\n\nbehaviors:\n",
            1,
        ),
        encoding="utf-8",
    )
    _, warnings = projects.import_project(str(source_dir), "rig-b")
    carried = systems_root / "rig-b" / "validation" / "check_http.sh"
    assert carried.is_file(), warnings
    assert carried.read_bytes() == (source_dir / "validation" / "check_http.sh").read_bytes()
