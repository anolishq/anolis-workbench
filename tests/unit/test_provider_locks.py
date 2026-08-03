"""The provider registry is the lock directory (#285).

Before #285 the set of provider kinds was declared three times — a glob over
the vendored envelopes plus a `_SCHEMA_CONFIGS` dict in each of the two schema
scripts — and none of the three was the sha-locked one.

These run in the ordinary suite as well as in CI's contract-drift job, because
`just test` does not invoke the verify script: without them a lock/envelope
disagreement is invisible locally until CI rejects the push.

Nothing here may name a provider kind. #285's whole claim is that adding one is
a data change, so a test that enumerates the current three would make the next
provider fail the suite — the very edit this issue exists to remove.
"""

from __future__ import annotations

import json
import pathlib

import provider_locks  # from scripts/, on the path via pytest's `pythonpath`
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Derived, never enumerated — see the module docstring.
LOCKED_KINDS = provider_locks.locked_kinds(REPO_ROOT)


# ---------------------------------------------------------------------------
# The live registry
# ---------------------------------------------------------------------------


def test_the_real_registry_is_consistent() -> None:
    """The whole point: locks and vendored envelopes agree, in both directions
    and on every field they share."""
    assert provider_locks.check_registry(REPO_ROOT) == []


def test_locks_and_envelopes_correspond_one_to_one() -> None:
    assert provider_locks.locked_kinds(REPO_ROOT) == provider_locks.vendored_kinds(REPO_ROOT)
    assert LOCKED_KINDS, "no provider locks found — the registry cannot be empty"


@pytest.mark.parametrize("kind", LOCKED_KINDS)
def test_every_lock_carries_the_v3_registry_fields(kind: str) -> None:
    lock = provider_locks.load_lock(provider_locks.lock_path(REPO_ROOT, kind))

    assert lock["schema_version"] == provider_locks.LOCK_SCHEMA_VERSION
    assert lock["kind"] == kind
    assert isinstance(lock["provider_version"], str) and lock["provider_version"].strip()
    templates = provider_locks.read_templates(lock, kind)
    assert "{version}" in templates["asset"]
    assert "{version}" in templates["manifest_asset"]


@pytest.mark.parametrize("kind", LOCKED_KINDS)
def test_the_lock_records_the_version_its_envelope_came_from(kind: str) -> None:
    """#283 resolves schemas on (kind, pinned version) and reports skew against
    `envelope.provider_version`. If the lock disagreed, the skew warning would
    name a version the vendored envelope did not come from."""
    lock = provider_locks.load_lock(provider_locks.lock_path(REPO_ROOT, kind))
    envelope = provider_locks.load_envelope(provider_locks.envelope_path(REPO_ROOT, kind))

    assert lock["provider_version"] == envelope["provider_version"]


# ---------------------------------------------------------------------------
# Kind validation — kinds build filenames and asset URLs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["../etc", "a/b", "Bread", "", "-lead", "sp ace", "x@1"])
def test_hostile_kinds_are_refused(kind: str) -> None:
    with pytest.raises(provider_locks.LockError):
        provider_locks.validate_kind(kind)


def test_paths_are_derived_from_the_kind_not_declared() -> None:
    """Recording the vendored path in the lock would be a second copy that
    could disagree with what `provider_schemas` actually globs."""
    assert provider_locks.envelope_path(REPO_ROOT, "sim").name == "sim.config-schema.json"
    assert provider_locks.lock_path(REPO_ROOT, "sim").name == "sim-config-schema.lock.json"


# ---------------------------------------------------------------------------
# Negative cases, against a synthetic registry
# ---------------------------------------------------------------------------


def _registry(tmp_path: pathlib.Path, kind: str = "foo", **overrides: object) -> pathlib.Path:
    """A minimal one-kind registry that passes, before overrides are applied."""
    version = "1.2.3"
    templates = provider_locks.asset_templates(kind)
    lock: dict[str, object] = {
        "schema_version": provider_locks.LOCK_SCHEMA_VERSION,
        "kind": kind,
        "provider_version": version,
        "source": {"repo": "vendor/anolis-provider-foo", "path": "x", "tag": f"v{version}"},
        "distribution": {
            "mode": "release-artifact",
            "asset_format": "raw",
            "templates": templates,
            "release": {
                "repo": "vendor/anolis-provider-foo",
                "tag": f"v{version}",
                "asset": templates["asset"].format(version=version),
                "manifest_asset": templates["manifest_asset"].format(version=version),
            },
            "schema_sha256": "0" * 64,
            "asset_sha256": "0" * 64,
        },
        "synced_at_utc": "2026-01-01T00:00:00Z",
    }
    lock.update(overrides)

    lock_file = provider_locks.lock_path(tmp_path, kind)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps(lock, indent=2), encoding="utf-8")

    env_file = provider_locks.envelope_path(tmp_path, kind)
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        json.dumps({"config_schema_version": 1, "provider_version": version, "schema": {}}),
        encoding="utf-8",
    )
    return tmp_path


def test_a_well_formed_synthetic_registry_passes(tmp_path: pathlib.Path) -> None:
    assert provider_locks.check_registry(_registry(tmp_path)) == []


def test_a_lock_with_no_envelope_is_a_kind_nothing_offers(tmp_path: pathlib.Path) -> None:
    root = _registry(tmp_path)
    provider_locks.envelope_path(root, "foo").unlink()

    problems = provider_locks.check_registry(root)
    assert any("no vendored envelope" in p for p in problems), problems


def test_an_envelope_with_no_lock_has_unrecorded_provenance(tmp_path: pathlib.Path) -> None:
    root = _registry(tmp_path)
    (root / provider_locks.ENVELOPE_DIR / "orphan.config-schema.json").write_text("{}", encoding="utf-8")

    problems = provider_locks.check_registry(root)
    assert any("orphan" in p and "no lock" in p for p in problems), problems


def test_a_lock_that_disagrees_with_its_envelope_version_is_caught(tmp_path: pathlib.Path) -> None:
    root = _registry(tmp_path, provider_version="9.9.9")

    problems = provider_locks.check_registry(root)
    assert any("provider_version" in p and "9.9.9" in p for p in problems), problems


def test_a_lock_whose_kind_contradicts_its_filename_is_caught(tmp_path: pathlib.Path) -> None:
    root = _registry(tmp_path, kind="bar")
    path = provider_locks.lock_path(root, "bar")
    lock = provider_locks.load_lock(path)
    lock["kind"] = "somethingelse"
    path.write_text(json.dumps(lock), encoding="utf-8")

    problems = provider_locks.check_registry(root)
    assert any("filename says" in p for p in problems), problems


def test_a_template_that_cannot_reproduce_its_pinned_asset_is_caught(tmp_path: pathlib.Path) -> None:
    """Otherwise the templates are a second, disagreeing copy of the asset name
    rather than the record a re-sync can trust."""
    root = _registry(tmp_path)
    path = provider_locks.lock_path(root, "foo")
    lock = provider_locks.load_lock(path)
    lock["distribution"]["templates"]["asset"] = "wrong-{version}.json"
    path.write_text(json.dumps(lock), encoding="utf-8")

    problems = provider_locks.check_registry(root)
    assert any("renders" in p and "wrong-1.2.3.json" in p for p in problems), problems


def test_an_unmigrated_v2_lock_is_caught(tmp_path: pathlib.Path) -> None:
    root = _registry(tmp_path, schema_version=2)

    problems = provider_locks.check_registry(root)
    assert any("schema_version is 2" in p for p in problems), problems


def test_a_v2_lock_missing_templates_is_reported_not_crashed(tmp_path: pathlib.Path) -> None:
    root = _registry(tmp_path)
    path = provider_locks.lock_path(root, "foo")
    lock = provider_locks.load_lock(path)
    del lock["distribution"]["templates"]
    path.write_text(json.dumps(lock), encoding="utf-8")

    problems = provider_locks.check_registry(root)
    assert any("distribution.templates" in p for p in problems), problems


def test_an_empty_registry_reports_nothing_rather_than_crashing(tmp_path: pathlib.Path) -> None:
    assert provider_locks.locked_kinds(tmp_path) == []
    assert provider_locks.vendored_kinds(tmp_path) == []
    assert provider_locks.check_registry(tmp_path) == []


# ---------------------------------------------------------------------------
# Malformed data must be REPORTED, not raised through
# ---------------------------------------------------------------------------


def test_an_illegal_lock_filename_is_reported_not_raised(tmp_path: pathlib.Path) -> None:
    """A case typo like `EZO-config-schema.lock.json` must not traceback out of
    the middle of CI — it is a data mistake like any other."""
    root = _registry(tmp_path)
    (root / provider_locks.LOCK_DIR / "Bad_Kind-config-schema.lock.json").write_text("{}", encoding="utf-8")
    (root / provider_locks.ENVELOPE_DIR / "Bad_Kind.config-schema.json").write_text("{}", encoding="utf-8")

    problems = provider_locks.check_registry(root)

    assert any("Bad_Kind" in p and "not a legal provider kind" in p for p in problems), problems
    # The legal kind alongside it is still checked rather than lost.
    assert provider_locks.locked_kinds(root) == ["foo"]


def test_a_template_with_an_unknown_placeholder_is_reported_not_raised(tmp_path: pathlib.Path) -> None:
    root = _registry(tmp_path)
    path = provider_locks.lock_path(root, "foo")
    lock = provider_locks.load_lock(path)
    lock["distribution"]["templates"]["asset"] = "a-{arch}-{version}.json"
    path.write_text(json.dumps(lock), encoding="utf-8")

    problems = provider_locks.check_registry(root)

    assert any("only the {version} placeholder" in p for p in problems), problems


@pytest.mark.parametrize("template", ["a-{}.json", "a-{0}.json", "a-{bogus}.json"])
def test_render_template_refuses_anything_but_version(template: str) -> None:
    with pytest.raises(provider_locks.LockError):
        provider_locks.render_template(template, "1.0.0")


def test_a_lock_missing_release_repo_is_caught(tmp_path: pathlib.Path) -> None:
    """Without it a re-sync silently falls back to the anolis org and 404s."""
    root = _registry(tmp_path)
    path = provider_locks.lock_path(root, "foo")
    lock = provider_locks.load_lock(path)
    del lock["distribution"]["release"]["repo"]
    path.write_text(json.dumps(lock), encoding="utf-8")

    problems = provider_locks.check_registry(root)

    assert any("release: missing non-empty string 'repo'" in p for p in problems), problems


def test_a_tag_that_disagrees_with_provider_version_is_caught(tmp_path: pathlib.Path) -> None:
    """install.sh builds provider asset names from the pinned version, so the
    tag version is what a machine pins. If the envelope declares something
    else, #283 reports skew on every machine and it can never be cleared."""
    root = _registry(tmp_path)
    path = provider_locks.lock_path(root, "foo")
    lock = provider_locks.load_lock(path)
    lock["distribution"]["release"]["tag"] = "v1.2.3-rc1"
    path.write_text(json.dumps(lock), encoding="utf-8")

    problems = provider_locks.check_registry(root)

    assert any("warn about skew forever" in p for p in problems), problems


def test_version_of_tag_strips_only_a_leading_v() -> None:
    assert provider_locks.version_of_tag("v1.2.3") == "1.2.3"
    assert provider_locks.version_of_tag("1.2.3") == "1.2.3"
    assert provider_locks.version_of_tag("2026.01.01") == "2026.01.01"
