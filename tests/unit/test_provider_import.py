"""provider import: a schema from a file, validated at its version (#783, #286).

Imports live under the data root, never in the package. Resolution is per
(kind, version), as #283 decided: an exact import wins, then the packaged
envelope, then a sideload-only kind's highest import. Every check here runs
against a temporary import directory; nothing touches the network.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from anolis_workbench.cli import main as cli_main
from anolis_workbench.core import provider_import, provider_schemas


@pytest.fixture(autouse=True)
def _imports_dir(monkeypatch, tmp_path):
    directory = tmp_path / "imports"
    monkeypatch.setattr(provider_schemas.paths_module, "IMPORTED_PROVIDER_SCHEMAS_DIR", directory)
    provider_schemas._envelope_cache = None
    provider_schemas._reset_imports_cache()
    yield directory
    provider_schemas._envelope_cache = None
    provider_schemas._reset_imports_cache()


def _packaged(kind: str) -> dict:
    envelope = provider_schemas._load_envelopes()[kind]
    copy: dict = json.loads(json.dumps(envelope))
    return copy


def _envelope(kind: str, version: str, *, extra_field: str | None = None, base: str = "sim") -> dict:
    doc = _packaged(base)
    doc["provider"] = f"anolis-provider-{kind}"
    doc["provider_version"] = version
    if extra_field:
        doc["schema"].setdefault("properties", {})[extra_field] = {"type": "string"}
    return doc


def _write(tmp_path: pathlib.Path, doc: object, name: str = "envelope.json") -> pathlib.Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _packaged_version(kind: str) -> str:
    version = _packaged(kind)["provider_version"]
    assert isinstance(version, str)
    return version


# --- resolution ---------------------------------------------------------------


def test_an_exact_import_is_used_for_its_pin_and_nothing_else(tmp_path):
    provider_import.import_file(_write(tmp_path, _envelope("bread", "0.9.0", base="bread")))

    exact = provider_schemas.resolve("bread", "0.9.0")
    assert exact is not None
    assert exact.source == "sideloaded"
    assert exact.exact
    assert exact.envelope["provider_version"] == "0.9.0"

    packaged = provider_schemas.resolve("bread", _packaged_version("bread"))
    assert packaged is not None and packaged.source == "packaged" and packaged.exact

    unpinned = provider_schemas.resolve("bread", None)
    assert unpinned is not None and unpinned.source == "packaged"


def test_an_exact_import_clears_the_skew_warning(tmp_path):
    document = {
        "providers": {"bread0": {"kind": "bread"}},
        "profile": {"components": {"providers": {"bread": {"version": "0.9.0"}}}},
    }
    assert provider_schemas.version_skew_warnings(document) != []
    provider_import.import_file(_write(tmp_path, _envelope("bread", "0.9.0", base="bread")))
    assert provider_schemas.version_skew_warnings(document) == []


def test_a_sideload_only_kind_falls_back_to_its_highest_import_with_a_warning(tmp_path):
    provider_import.import_file(_write(tmp_path, _envelope("foo", "0.1.0"), "a.json"))
    provider_import.import_file(_write(tmp_path, _envelope("foo", "0.10.0"), "b.json"))
    provider_import.import_file(_write(tmp_path, _envelope("foo", "0.2.0"), "c.json"))

    assert "foo" in provider_schemas.available_kinds()
    resolution = provider_schemas.resolve("foo", "0.11.0")
    assert resolution is not None
    assert resolution.source == "sideloaded"
    # Semver order, not string order: 0.10.0 is above 0.2.0.
    assert resolution.envelope_version == "0.10.0"
    assert not resolution.exact
    assert resolution.skew_message() is not None
    envelope = provider_schemas.get_envelope("foo")
    assert envelope is not None and envelope["provider_version"] == "0.10.0"


def test_an_import_is_seen_without_a_restart(tmp_path):
    # Warm every cache first, as a running server has.
    assert "foo" not in provider_schemas.available_kinds()
    assert provider_schemas.resolve("foo", "0.1.0") is None
    provider_import.import_file(_write(tmp_path, _envelope("foo", "0.1.0")))
    assert "foo" in provider_schemas.available_kinds()
    resolution = provider_schemas.resolve("foo", "0.1.0")
    assert resolution is not None and resolution.exact


# --- the import itself ----------------------------------------------------------


def test_an_import_stores_the_bytes_given_and_a_sidecar(tmp_path, _imports_dir):
    source = _write(tmp_path, _envelope("foo", "0.1.0"))
    result = provider_import.import_file(source)
    assert result.kind == "foo" and result.version == "0.1.0" and result.status == "imported"

    stored = _imports_dir / "foo@0.1.0.config-schema.json"
    assert stored.read_bytes() == source.read_bytes()
    sidecar = json.loads((_imports_dir / "foo@0.1.0.source.json").read_text(encoding="utf-8"))
    assert sidecar["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert sidecar["origin"] == str(source.resolve())
    assert sidecar["provider_version"] == "0.1.0"
    assert sidecar["imported_at_utc"].endswith("Z")
    # No temporary file is left beside them.
    assert sorted(p.name for p in _imports_dir.iterdir()) == [
        "foo@0.1.0.config-schema.json",
        "foo@0.1.0.source.json",
    ]


def _deep(levels: int) -> dict:
    node: dict = {"type": "string"}
    for _ in range(levels):
        node = {"type": "object", "properties": {"x": node}}
    return node


def _with(**changes) -> dict:
    doc = _envelope("foo", "0.1.0")
    for key, value in changes.items():
        if key == "schema_patch":
            doc["schema"].update(value)
        elif value is None:
            doc.pop(key, None)
        else:
            doc[key] = value
    return doc


@pytest.mark.parametrize(
    ("doc", "reason"),
    [
        (_with(schema_patch={"properties": {"a": {"$ref": "http://127.0.0.1:9/x.json"}}}), "$ref"),
        (_with(schema_patch={"properties": {"a": {"$dynamicRef": "#x"}}}), "$ref"),
        (_with(schema_patch={"type": 5}), "not a valid JSON Schema"),
        (_with(provider_version="0.9"), "semver"),
        (_with(provider_version="v0.9.0"), "semver"),
        (_with(provider_version=None), "provider_version"),
        (_with(provider="anolis-provider-foo.bar"), "kind"),
        (_with(provider="anolis-provider-../x"), "kind"),
        (_with(provider="anolis-provider-Foo"), "kind"),
        (_with(provider="other-foo"), "anolis-provider-"),
        (_with(config_schema_version=0), "config_schema_version"),
        (_with(schema_patch={"properties": {"deep": _deep(40)}}), "deep"),
        ("not json", "JSON"),
        (["a list"], "JSON object"),
    ],
)
def test_a_bad_file_is_refused_and_nothing_is_stored(tmp_path, _imports_dir, doc, reason):
    path = tmp_path / "bad.json"
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    with pytest.raises(provider_import.ImportRefused, match=__import__("re").escape(reason)):
        provider_import.import_file(path)
    assert not _imports_dir.exists() or list(_imports_dir.iterdir()) == []


def test_an_oversized_file_is_refused(tmp_path, _imports_dir):
    doc = _envelope("foo", "0.1.0")
    doc["schema"]["description"] = "x" * (provider_import.MAX_ENVELOPE_BYTES + 1)
    with pytest.raises(provider_import.ImportRefused, match="bytes"):
        provider_import.import_file(_write(tmp_path, doc))
    assert not _imports_dir.exists() or list(_imports_dir.iterdir()) == []


def test_a_packaged_kind_and_version_cannot_be_imported_with_other_bytes(tmp_path):
    version = _packaged_version("bread")
    doc = _envelope("bread", version, base="bread", extra_field="smuggled")
    with pytest.raises(provider_import.ImportRefused, match="packaged"):
        provider_import.import_file(_write(tmp_path, doc))
    resolution = provider_schemas.resolve("bread", version)
    assert resolution is not None and resolution.source == "packaged"
    assert "smuggled" not in resolution.envelope["schema"].get("properties", {})


def test_the_packaged_envelope_itself_imports_as_a_no_op(tmp_path, _imports_dir):
    result = provider_import.import_file(_write(tmp_path, _packaged("bread")))
    assert result.status == "already packaged"
    assert not _imports_dir.exists() or list(_imports_dir.iterdir()) == []


def test_an_import_is_immutable_unless_replaced(tmp_path):
    first = _write(tmp_path, _envelope("foo", "0.1.0"), "first.json")
    assert provider_import.import_file(first).status == "imported"
    assert provider_import.import_file(first).status == "unchanged"

    changed = _write(tmp_path, _envelope("foo", "0.1.0", extra_field="later"), "changed.json")
    with pytest.raises(provider_import.ImportRefused, match="--replace"):
        provider_import.import_file(changed)
    resolution = provider_schemas.resolve("foo", "0.1.0")
    assert resolution is not None
    assert "later" not in resolution.envelope["schema"].get("properties", {})

    assert provider_import.import_file(changed, replace=True).status == "replaced"
    resolution = provider_schemas.resolve("foo", "0.1.0")
    assert resolution is not None
    assert "later" in resolution.envelope["schema"]["properties"]


# --- a bad file on disk -------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "{ not json",
        json.dumps(_with(schema_patch={"properties": {"a": {"$ref": "file:///etc/passwd"}}})),
        json.dumps(_with(provider_version="0.2.0")),  # disagrees with its file name
    ],
)
def test_a_bad_import_on_disk_is_skipped_and_listed_never_raised(tmp_path, _imports_dir, content):
    provider_import.import_file(_write(tmp_path, _envelope("foo", "0.1.0")))
    _imports_dir.joinpath("foo@0.3.0.config-schema.json").write_text(content, encoding="utf-8")

    kinds = provider_schemas.available_kinds()  # raises on a bad file today
    assert "foo" in kinds and "bread" in kinds
    resolution = provider_schemas.resolve("foo", "0.3.0")
    assert resolution is not None and resolution.envelope_version == "0.1.0"

    listing = provider_import.list_imports()
    bad = [entry for entry in listing if entry.error]
    assert [entry.file for entry in bad] == ["foo@0.3.0.config-schema.json"]


def test_a_packaged_envelope_still_fails_loudly(monkeypatch, tmp_path):
    """Packaged envelopes are CI-verified; a broken one is a packaging bug."""
    monkeypatch.setattr(provider_schemas.paths_module, "PROVIDER_SCHEMAS_DIR", tmp_path)
    (tmp_path / "x.config-schema.json").write_text("{ not json", encoding="utf-8")
    provider_schemas._envelope_cache = None
    with pytest.raises(ValueError):
        provider_schemas.available_kinds()


# --- CLI -----------------------------------------------------------------------------


def test_cli_imports_and_lists(tmp_path, capsys):
    source = _write(tmp_path, _envelope("foo", "0.1.0"))
    assert cli_main.main(["provider", "import", str(source)]) == 0
    out = capsys.readouterr().out
    assert "foo 0.1.0" in out and "imported" in out

    assert cli_main.main(["provider", "list"]) == 0
    out = capsys.readouterr().out
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert "foo" in out and "0.1.0" in out and sha[:12] in out and str(source.resolve()) in out


def test_cli_refusal_exits_nonzero_with_the_reason(tmp_path, capsys):
    path = _write(tmp_path, _with(provider_version="0.9"))
    assert cli_main.main(["provider", "import", str(path)]) == 1
    assert "semver" in capsys.readouterr().err


def test_cli_warns_when_run_as_root(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli_main.os, "geteuid", lambda: 0, raising=False)
    assert cli_main.main(["provider", "import", str(_write(tmp_path, _envelope("foo", "0.1.0")))]) == 0
    assert "root" in capsys.readouterr().err


def test_bare_cli_still_serves(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(cli_main, "run_server", lambda **kw: calls.append(kw))
    cli_main.main([])
    cli_main.main(["--port", "3011", "--no-browser"])
    assert calls == [
        {"host": None, "port": None, "open_browser": None},
        {"host": None, "port": 3011, "open_browser": False},
    ]


# --- through the validation a save runs --------------------------------------------


def test_a_project_save_validates_an_imported_kind_against_its_import(tmp_path):
    from anolis_workbench.core import projects

    envelope = {
        "config_schema_version": 1,
        "provider": "anolis-provider-foo",
        "provider_version": "0.1.0",
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["port"],
            "properties": {"port": {"type": "string"}},
            "additionalProperties": False,
        },
    }

    def errors(config: dict) -> list[dict[str, str]]:
        return projects._validate_canonical_provider_configs(
            {
                "providers": {"foo0": {"kind": "foo", "config": config}},
                "profile": {"components": {"providers": {"foo": {"version": "0.1.0"}}}},
            }
        )

    assert [e["code"] for e in errors({"port": "/dev/ttyUSB0"})] == ["provider.unknown_kind"]
    provider_import.import_file(_write(tmp_path, envelope))
    assert errors({"port": "/dev/ttyUSB0"}) == []
    refused = errors({})
    assert [e["code"] for e in refused] == ["provider.schema"]
    assert "port" in refused[0]["message"]


# --- from the adversarial review of #788 -------------------------------------------


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        # json.loads takes these, and the route's json.dumps writes them back
        # as bare NaN/Infinity, which no browser can parse.
        (
            '{"config_schema_version": 1, "provider": "anolis-provider-foo", "provider_version": "1.0.0",'
            ' "schema": {"type": "number", "minimum": NaN}}',
            "finite",
        ),
        (
            '{"config_schema_version": 1, "provider": "anolis-provider-foo", "provider_version": "1.0.0",'
            ' "schema": {"type": "number", "maximum": Infinity}}',
            "finite",
        ),
        (
            '{"config_schema_version": 1, "provider": "anolis-provider-foo", "provider_version": "1.0.0",'
            ' "schema": {"type": "number", "maximum": 1e999}}',
            "finite",
        ),
        # re.compile raises OverflowError, not re.error.
        (
            '{"config_schema_version": 1, "provider": "anolis-provider-foo", "provider_version": "1.0.0",'
            ' "schema": {"type": "string", "pattern": "a{4294967296}"}}',
            "JSON Schema",
        ),
        (
            '{"config_schema_version": 1, "provider": "anolis-provider-foo", "provider_version": "1.0.0",'
            ' "schema": {"type": "object", "patternProperties": {"a{4294967296}": {}}}}',
            "JSON Schema",
        ),
        # The decoder recurses before the depth check sees anything.
        ("[" * 50000, "JSON"),
        # $ matches before a trailing newline.
        (
            '{"config_schema_version": 1, "provider": "anolis-provider-foo\\n", "provider_version": "1.0.0",'
            ' "schema": {}}',
            "kind",
        ),
        (
            '{"config_schema_version": 1, "provider": "anolis-provider-foo", "provider_version": "1.0.0\\n",'
            ' "schema": {}}',
            "semver",
        ),
        # A long prerelease makes a file name over 255 bytes.
        (
            '{"config_schema_version": 1, "provider": "anolis-provider-foo", "provider_version": "1.0.0-'
            + "a" * 300
            + '", "schema": {}}',
            "too long \\(more than",
        ),
        (
            '{"config_schema_version": 1, "provider": "anolis-provider-' + "f" * 100 + '", "provider_version":'
            ' "1.0.0", "schema": {}}',
            "too long \\(more than",
        ),
    ],
)
def test_review_cases_are_refused_cleanly(tmp_path, _imports_dir, raw, reason):
    path = tmp_path / "bad.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(provider_import.ImportRefused, match=reason):
        provider_import.import_file(path)
    assert not _imports_dir.exists() or list(_imports_dir.iterdir()) == []


@pytest.mark.parametrize(
    "raw",
    [
        '{"config_schema_version": 1, "provider": "anolis-provider-foo", "provider_version": "0.3.0",'
        ' "schema": {"type": "string", "pattern": "a{4294967296}"}}',
        "[" * 50000,
        '{"config_schema_version": 1, "provider": "anolis-provider-foo", "provider_version": "0.3.0",'
        ' "schema": {"type": "number", "minimum": NaN}}',
    ],
)
def test_no_file_on_disk_breaks_a_save_startup_or_the_listing(tmp_path, _imports_dir, raw):
    from anolis_workbench.core import projects
    from anolis_workbench.server import app

    provider_import.import_file(_write(tmp_path, _envelope("foo", "0.1.0")))
    _imports_dir.joinpath("foo@0.3.0.config-schema.json").write_text(raw, encoding="utf-8")

    # A save of a project that uses only a packaged kind.
    sim = provider_schemas.get_envelope("sim")
    assert sim is not None
    errors = projects._validate_canonical_provider_configs({"providers": {"sim0": {"kind": "sim", "config": {}}}})
    assert all(e["code"] != "provider.unknown_kind" for e in errors)
    app.verify_environment()
    assert [e.file for e in provider_import.list_imports() if e.error] == ["foo@0.3.0.config-schema.json"]
    json.loads(json.dumps(provider_schemas.all_envelopes(), allow_nan=False))


def test_an_import_of_a_version_later_packaged_is_superseded(monkeypatch, tmp_path):
    """A workbench upgrade that packages an already-imported version wins."""
    provider_import.import_file(_write(tmp_path, _envelope("sim", "9.9.9", extra_field="only_in_import")))
    resolution = provider_schemas.resolve("sim", "9.9.9")
    assert resolution is not None and resolution.source == "sideloaded"

    packaged = dict(provider_schemas._load_envelopes())
    upgraded = json.loads(json.dumps(packaged["sim"]))
    upgraded["provider_version"] = "9.9.9"
    monkeypatch.setattr(provider_schemas, "_envelope_cache", {**packaged, "sim": upgraded})
    provider_schemas._reset_imports_cache()

    resolution = provider_schemas.resolve("sim", "9.9.9")
    assert resolution is not None and resolution.source == "packaged"
    assert "only_in_import" not in resolution.envelope["schema"].get("properties", {})
    assert "superseded" in provider_schemas.import_errors()["sim@9.9.9.config-schema.json"]


def test_the_startup_check_still_needs_packaged_envelopes(monkeypatch, tmp_path):
    from anolis_workbench.server import app

    provider_import.import_file(_write(tmp_path, _envelope("foo", "0.1.0")))
    monkeypatch.setattr(provider_schemas.paths_module, "PROVIDER_SCHEMAS_DIR", tmp_path / "empty")
    provider_schemas._envelope_cache = None
    with pytest.raises(SystemExit):
        app.verify_environment()


def test_a_concurrent_import_of_the_same_version_does_not_overwrite(monkeypatch, tmp_path, _imports_dir):
    """Import B lands while A is between its check and its write."""
    a = _write(tmp_path, _envelope("foo", "0.1.0"), "a.json")
    b = _write(tmp_path, _envelope("foo", "0.1.0", extra_field="b"), "b.json")
    real = provider_import._write_temp
    raced = {"done": False}

    def write(directory, name, raw):
        if not raced["done"]:
            raced["done"] = True
            provider_import.import_file(b)
        return real(directory, name, raw)

    monkeypatch.setattr(provider_import, "_write_temp", write)
    with pytest.raises(provider_import.ImportRefused, match="--replace"):
        provider_import.import_file(a)
    stored = json.loads((_imports_dir / "foo@0.1.0.config-schema.json").read_text(encoding="utf-8"))
    assert "b" in stored["schema"]["properties"]
    entry = next(e for e in provider_import.list_imports() if e.file == "foo@0.1.0.config-schema.json")
    assert entry.origin == str(b.resolve())


def test_prereleases_order_as_semver():
    key = provider_import.semver_key
    assert key("1.0.0-rc.10") > key("1.0.0-rc.2")
    assert key("1.0.0-alpha") < key("1.0.0-alpha.1") < key("1.0.0-beta") < key("1.0.0")
    assert key("1.0.0-2") < key("1.0.0-a")


def test_a_sidecar_for_other_content_is_not_believed(tmp_path, _imports_dir):
    provider_import.import_file(_write(tmp_path, _envelope("foo", "0.1.0")))
    sidecar = _imports_dir / "foo@0.1.0.source.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["sha256"] = "0" * 64
    data["origin"] = "/somewhere/else.json"
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    (entry,) = provider_import.list_imports()
    assert entry.origin is None and entry.imported_at_utc is None and entry.error is None


@pytest.mark.parametrize("replace", [False, True])
def test_a_version_differing_only_in_case_is_refused(tmp_path, _imports_dir, replace):
    # macOS and Windows file systems ignore case by default, so foo@1.0.0-RC
    # and foo@1.0.0-rc would share one file there (#787).
    provider_import.import_file(_write(tmp_path, _envelope("foo", "1.0.0-RC"), "upper.json"))
    before = sorted(p.name for p in _imports_dir.iterdir())
    with pytest.raises(provider_import.ImportRefused, match=r"foo@1\.0\.0-RC"):
        provider_import.import_file(_write(tmp_path, _envelope("foo", "1.0.0-rc"), "lower.json"), replace=replace)
    assert sorted(p.name for p in _imports_dir.iterdir()) == before
