"""An imported kind deploys from its own repository (#785, #286).

install.sh fetches a provider's release from components.providers.<kind>.repo.
An import records that repo (--repo, else anolishq/anolis-provider-<kind>),
the resolve route hands it to the composer, and the composer seeds a new pin
from it, so a third-party kind isn't fetched from the wrong org.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml

from anolis_workbench.cli import main as cli_main
from anolis_workbench.core import canonical, deploy, machine_profile, projects, provider_import, provider_schemas


@pytest.fixture(autouse=True)
def _imports_dir(monkeypatch, tmp_path):
    directory = tmp_path / "data" / "schemas" / "providers"
    monkeypatch.setattr(provider_schemas.paths_module, "IMPORTED_PROVIDER_SCHEMAS_DIR", directory)
    provider_schemas._envelope_cache = None
    provider_schemas._reset_imports_cache()
    yield directory
    provider_schemas._envelope_cache = None
    provider_schemas._reset_imports_cache()


def _source(
    tmp_path: pathlib.Path, kind: str = "foo", version: str = "0.1.0", extra: str | None = None
) -> pathlib.Path:
    doc = json.loads(json.dumps(provider_schemas._load_envelopes()["sim"]))
    doc["provider"] = f"anolis-provider-{kind}"
    doc["provider_version"] = version
    if extra:
        doc["schema"].setdefault("properties", {})[extra] = {"type": "string"}
    path = tmp_path / f"{kind}-{version}-{extra or 'base'}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _entry(kind: str, version: str) -> provider_import.ImportEntry:
    (entry,) = [e for e in provider_import.list_imports() if e.kind == kind and e.version == version]
    return entry


def test_an_import_records_the_repo_it_was_given(tmp_path):
    result = provider_import.import_file(_source(tmp_path), repo="example/anolis-provider-foo")
    assert result.repo == "example/anolis-provider-foo"
    assert _entry("foo", "0.1.0").repo == "example/anolis-provider-foo"


def test_without_a_repo_the_import_defaults_to_the_anolishq_name(tmp_path):
    assert provider_import.import_file(_source(tmp_path)).repo == "anolishq/anolis-provider-foo"
    assert _entry("foo", "0.1.0").repo == "anolishq/anolis-provider-foo"


@pytest.mark.parametrize(
    "repo",
    [
        "noslash",
        "a/b/c",
        "../x",
        "owner/..",
        "owner/.",
        "-owner/x",
        "own er/x",
        "owner/x y",
        "owner/",
        "/x",
        "a" * 40 + "/x",
    ],
)
def test_a_repo_that_isnt_owner_slash_name_is_refused(tmp_path, _imports_dir, repo):
    with pytest.raises(provider_import.ImportRefused, match="repo"):
        provider_import.import_file(_source(tmp_path), repo=repo)
    assert not _imports_dir.exists() or list(_imports_dir.iterdir()) == []


def test_the_same_file_with_a_new_repo_updates_only_the_repo(tmp_path):
    source = _source(tmp_path)
    provider_import.import_file(source, repo="example/first")
    result = provider_import.import_file(source, repo="example/second")
    assert result.status == "repo updated"
    entry = _entry("foo", "0.1.0")
    assert entry.repo == "example/second" and entry.origin == str(source.resolve())


def test_the_same_file_without_a_repo_keeps_the_recorded_one(tmp_path):
    source = _source(tmp_path)
    provider_import.import_file(source, repo="example/anolis-provider-foo")
    assert provider_import.import_file(source).status == "unchanged"
    assert _entry("foo", "0.1.0").repo == "example/anolis-provider-foo"


def test_a_replace_without_a_repo_keeps_the_recorded_one(tmp_path):
    provider_import.import_file(_source(tmp_path), repo="example/anolis-provider-foo")
    provider_import.import_file(_source(tmp_path, extra="later"), replace=True)
    assert _entry("foo", "0.1.0").repo == "example/anolis-provider-foo"


def test_the_resolution_carries_an_imports_repo_and_none_for_a_packaged_kind(tmp_path):
    provider_import.import_file(_source(tmp_path), repo="example/anolis-provider-foo")
    resolution = provider_schemas.resolve_pins({})["resolution"]
    assert resolution["foo"]["repo"] == "example/anolis-provider-foo"
    assert resolution["sim"]["repo"] is None


def test_a_project_with_an_imported_kind_deploys_with_its_repo(tmp_path):
    provider_import.import_file(_source(tmp_path), repo="example/anolis-provider-foo")
    projects.create_project_from_template("rig-a", "sim-quickstart")
    pdir = projects.project_dir("rig-a")
    profile = machine_profile.load_profile(pdir)
    profile["components"]["providers"]["foo"] = {"repo": "example/anolis-provider-foo", "version": "0.1.0"}
    canonical._write_yaml_atomic(pdir / machine_profile.PROFILE_FILENAME, profile)

    out = deploy.materialize_project_dir(pdir, tmp_path / "materialized")
    shipped = yaml.safe_load((out.project_dir / machine_profile.PROFILE_FILENAME).read_text(encoding="utf-8"))
    assert shipped["components"]["providers"]["foo"]["repo"] == "example/anolis-provider-foo"
    assert shipped["components"]["providers"]["sim"]["repo"] == "anolishq/anolis-provider-sim"


def test_cli_takes_and_lists_the_repo(tmp_path, capsys):
    source = _source(tmp_path)
    assert cli_main.main(["provider", "import", str(source), "--repo", "example/anolis-provider-foo"]) == 0
    assert "example/anolis-provider-foo" in capsys.readouterr().out
    assert cli_main.main(["provider", "list"]) == 0
    assert "example/anolis-provider-foo" in capsys.readouterr().out
    assert cli_main.main(["provider", "import", str(source), "--repo", "../evil"]) == 1
    assert "repo" in capsys.readouterr().err
