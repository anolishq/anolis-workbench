"""The sync script's argument surface and its round trip into the registry (#285).

`sync-upstream-schema-from-release.py` writes the lock files that
`verify-upstream-schema.py` then polices, so the pairing that matters is: does
the tool ever produce a lock its own verifier rejects? Everything here drives
the real `main()` with the network stubbed, rather than reimplementing it.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types

import provider_locks  # from scripts/, on the path via pytest's `pythonpath`
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load_sync() -> types.ModuleType:
    """The script's filename is hyphenated, so it needs a path-based import."""
    spec = importlib.util.spec_from_file_location("sync_upstream", SCRIPTS / "sync-upstream-schema-from-release.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = _load_sync()


ENVELOPE = {
    "config_schema_version": 1,
    "provider": "anolis-provider-acme",
    "provider_version": "1.0.0",
    "schema": {"type": "object"},
}


def _stub_network(monkeypatch: pytest.MonkeyPatch, envelope: dict) -> None:
    """Serve the envelope for the asset URL and a matching manifest sidecar."""
    payload = json.dumps(envelope).encode("utf-8")
    digest = sync.sha256_bytes(payload)

    def _fetch(url: str, timeout_seconds: int = 45) -> bytes:
        if url.endswith("-manifest.json"):
            asset = url.rsplit("/", 1)[-1].replace("-manifest.json", ".json")
            return json.dumps({"asset": asset, "sha256": digest}).encode("utf-8")
        return payload

    monkeypatch.setattr(sync, "fetch_url_bytes", _fetch)


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["sync-upstream-schema-from-release.py", *argv])
    return int(sync.main())


# ---------------------------------------------------------------------------
# The round trip: never write a lock our own verifier rejects
# ---------------------------------------------------------------------------


def test_onboarding_a_new_kind_produces_a_lock_the_verifier_accepts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """The acceptance criterion of #285, end to end: no code change, and the
    result passes the registry checks CI runs."""
    _stub_network(monkeypatch, ENVELOPE)

    rc = _run(
        monkeypatch,
        [
            "--new-provider",
            "acme",
            "--repo",
            "vendor/anolis-provider-acme",
            "--tag",
            "v1.0.0",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0
    assert provider_locks.check_registry(tmp_path) == []
    lock = provider_locks.load_lock(provider_locks.lock_path(tmp_path, "acme"))
    assert lock["schema_version"] == provider_locks.LOCK_SCHEMA_VERSION
    assert lock["kind"] == "acme"
    assert lock["provider_version"] == "1.0.0"
    assert lock["distribution"]["release"]["repo"] == "vendor/anolis-provider-acme"
    assert provider_locks.envelope_path(tmp_path, "acme").is_file()


def test_a_custom_asset_template_round_trips_through_the_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A vendor whose naming differs passes the templates once; every later
    sync reads them back out rather than needing the flag again."""
    _stub_network(monkeypatch, ENVELOPE)

    rc = _run(
        monkeypatch,
        [
            "--new-provider",
            "acme",
            "--repo",
            "vendor/anolis-provider-acme",
            "--tag",
            "v1.0.0",
            "--asset-template",
            "weird-{version}-schema.json",
            "--manifest-asset-template",
            "weird-{version}-schema-manifest.json",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0
    assert provider_locks.check_registry(tmp_path) == []
    plan = sync._plan_for_existing_provider(tmp_path, "acme")
    assert plan["asset_template"] == "weird-{version}-schema.json"
    assert plan["default_repo"] == "vendor/anolis-provider-acme"


def test_a_tag_disagreeing_with_the_envelope_is_refused_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Otherwise the tool writes a lock its own verifier rejects, with exit 0 —
    and #283 would report unclearable skew on every machine using it."""
    _stub_network(monkeypatch, {**ENVELOPE, "provider_version": "1.0.0-rc1"})

    rc = _run(
        monkeypatch,
        [
            "--new-provider",
            "acme",
            "--repo",
            "vendor/anolis-provider-acme",
            "--tag",
            "v1.0.0",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 1
    # No half-written state: an envelope with no lock is itself a CI failure.
    assert not provider_locks.envelope_path(tmp_path, "acme").is_file()
    assert not provider_locks.lock_path(tmp_path, "acme").is_file()


def test_an_envelope_without_provider_version_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    envelope = {k: v for k, v in ENVELOPE.items() if k != "provider_version"}
    _stub_network(monkeypatch, envelope)

    with pytest.raises(RuntimeError, match="provider_version"):
        _run(
            monkeypatch,
            [
                "--new-provider",
                "acme",
                "--repo",
                "vendor/anolis-provider-acme",
                "--tag",
                "v1.0.0",
                "--repo-root",
                str(tmp_path),
            ],
        )


# ---------------------------------------------------------------------------
# Argument surface
# ---------------------------------------------------------------------------


def test_repo_root_composes_with_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`--schema` is validated against the RESOLVED root. Building argparse
    `choices` up front would validate against this repo's kinds instead."""
    _stub_network(monkeypatch, ENVELOPE)
    _run(
        monkeypatch,
        [
            "--new-provider",
            "acme",
            "--repo",
            "vendor/anolis-provider-acme",
            "--tag",
            "v1.0.0",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert "provider-config-acme" in sync.known_schemas(tmp_path)
    assert "provider-config-acme" not in sync.known_schemas(REPO_ROOT)

    rc = _run(monkeypatch, ["--schema", "provider-config-acme", "--tag", "v1.0.0", "--repo-root", str(tmp_path)])
    assert rc == 0


def test_a_kind_this_repo_does_not_lock_is_rejected_with_the_valid_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["--schema", "provider-config-nope", "--tag", "v1.0.0"])


@pytest.mark.parametrize(
    "argv",
    [
        # --new-provider has no lock to read the repo from.
        ["--new-provider", "acme", "--tag", "v1.0.0"],
        # --repo only applies to onboarding.
        ["--schema", "provider-config-sim", "--repo", "x/y", "--tag", "v1.0.0"],
        # Two different ways to name the source repo.
        ["--new-provider", "acme", "--repo", "x/y", "--upstream-repo", "a/b", "--tag", "v1.0.0"],
        # Mutually exclusive targets.
        ["--schema", "provider-config-sim", "--new-provider", "acme", "--tag", "v1.0.0"],
        # A target is required.
        ["--tag", "v1.0.0"],
    ],
)
def test_contradictory_arguments_are_errors_not_silent_wrong_results(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    with pytest.raises(SystemExit):
        _run(monkeypatch, argv)


def test_onboarding_a_kind_that_already_has_a_lock_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    rc = _run(monkeypatch, ["--new-provider", "sim", "--repo", "x/y", "--tag", "v1.0.0"])
    assert rc == 1


def test_template_overrides_are_refused_for_an_existing_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """They live in the lock once onboarded; accepting them here would write a
    lock whose templates no longer describe how it was fetched."""
    rc = _run(
        monkeypatch,
        ["--schema", "provider-config-sim", "--tag", "v0.2.7", "--asset-template", "x-{version}.json"],
    )
    assert rc == 1


def test_a_lock_without_release_repo_refuses_to_guess(tmp_path: pathlib.Path) -> None:
    """Falling back to the anolis default would fetch a provider asset from the
    wrong org."""
    lock_file = provider_locks.lock_path(tmp_path, "acme")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "kind": "acme",
                "provider_version": "1.0.0",
                "distribution": {
                    "templates": provider_locks.asset_templates("acme"),
                    "release": {"tag": "v1.0.0"},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(provider_locks.LockError, match="release.repo"):
        sync._plan_for_existing_provider(tmp_path, "acme")
