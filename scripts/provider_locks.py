#!/usr/bin/env python3
"""The provider registry: `contracts/upstream/providers/*.lock.json` (#285).

There is exactly one declaration per provider kind, and this is it. Before
#285 the kind list existed three times — a glob over the vendored envelopes,
`_SCHEMA_CONFIGS` in the sync script, and `_SCHEMA_CONFIGS` in the verify
script — and none of the three was the sha-locked one. Worse, it was circular:
`--schema` was `choices=list(_SCHEMA_CONFIGS.keys())`, so a kind had to already
be in the dict before it could be synced.

The lock carries `{kind, provider_version, repo, tag, asset, sha256}` plus the
asset-name TEMPLATES, which is what breaks the circularity: a re-sync reads the
templates back out of the lock, and only a brand-new kind synthesises them from
the naming convention.

NOT importable from `anolis_workbench`, deliberately. The wheel ships the
package plus `schemas/**`, `templates/**` and `frontend/dist/**`; `contracts/`
is repo-root and is NOT packaged, so a lock file cannot reach a target host —
it must never become something the runtime depends on. The set of kinds the workbench OFFERS
is still the set of vendored envelopes; this is the vendoring-time record of
where each of those came from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

LOCK_SCHEMA_VERSION = 3

LOCK_DIR = Path("contracts/upstream/providers")
ENVELOPE_DIR = Path("anolis_workbench/schemas/providers")

_LOCK_SUFFIX = "-config-schema.lock.json"
_ENVELOPE_SUFFIX = ".config-schema.json"

# Kinds are used to build filenames and asset names, so keep them boring.
_KIND_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class LockError(RuntimeError):
    """A provider lock is missing, malformed, or disagrees with its envelope."""


def validate_kind(kind: str) -> str:
    if not isinstance(kind, str) or not _KIND_RE.match(kind):
        raise LockError(f"provider kind must match {_KIND_RE.pattern}: {kind!r}")
    return kind


def lock_path(repo_root: Path, kind: str) -> Path:
    return repo_root / LOCK_DIR / f"{validate_kind(kind)}{_LOCK_SUFFIX}"


def envelope_path(repo_root: Path, kind: str) -> Path:
    """Derived, not declared: the vendored envelope location is a convention
    (`provider_schemas._load_envelopes` globs exactly this), so recording it in
    the lock would be a second copy that could disagree."""
    return repo_root / ENVELOPE_DIR / f"{validate_kind(kind)}{_ENVELOPE_SUFFIX}"


def _stems(directory: Path, suffix: str) -> tuple[list[str], list[str]]:
    """Filename stems in `directory`, split into (legal kinds, illegal names).

    Illegal names are RETURNED rather than raised on: a `EZO-config-schema.lock.json`
    typo is a data mistake the registry check should report alongside the
    others, not a traceback out of the middle of CI.
    """
    if not directory.is_dir():
        return [], []
    legal: list[str] = []
    illegal: list[str] = []
    for path in sorted(directory.glob(f"*{suffix}")):
        stem = path.name[: -len(suffix)]
        (legal if _KIND_RE.match(stem) else illegal).append(stem)
    return legal, illegal


def locked_kinds(repo_root: Path) -> list[str]:
    """Every provider kind with a lock, from the filenames."""
    return _stems(repo_root / LOCK_DIR, _LOCK_SUFFIX)[0]


def vendored_kinds(repo_root: Path) -> list[str]:
    """Every provider kind with a vendored envelope — the set the workbench
    actually offers at runtime."""
    return _stems(repo_root / ENVELOPE_DIR, _ENVELOPE_SUFFIX)[0]


def load_lock(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LockError(f"failed to parse lock file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LockError(f"lock file root must be a JSON object: {path}")
    return raw


def load_envelope(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LockError(f"failed to parse provider envelope {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LockError(f"provider envelope root must be a JSON object: {path}")
    return raw


def version_of_tag(tag: str) -> str:
    """The version an asset name is built from. install.sh derives provider
    asset names the same way, so this is the version a machine PINS."""
    return tag[1:] if tag.startswith("v") else tag


def render_template(template: str, version: str) -> str:
    """`template.format(version=...)`, with a useful error instead of a crash.

    `--asset-template` is caller-supplied, so an unknown placeholder must be
    reported as bad data rather than raising KeyError/IndexError out of the
    middle of a CI job.
    """
    try:
        return template.format(version=version)
    except (KeyError, IndexError, ValueError) as exc:
        raise LockError(
            f"asset name template {template!r} must use only the {{version}} placeholder ({exc.__class__.__name__})"
        ) from exc


def asset_templates(kind: str) -> dict[str, str]:
    """The naming convention, used ONLY when onboarding a kind that has no lock
    yet. Every later sync reads the templates back out of the lock, so a vendor
    who names assets differently edits the lock once instead of this file."""
    validate_kind(kind)
    return {
        "asset": f"anolis-provider-{kind}-{{version}}-config-schema.json",
        "manifest_asset": f"anolis-provider-{kind}-{{version}}-config-schema-manifest.json",
    }


def _require_str(container: dict[str, Any], key: str, where: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LockError(f"{where}: missing non-empty string '{key}'")
    return value


def read_templates(lock: dict[str, Any], where: str) -> dict[str, str]:
    distribution = lock.get("distribution")
    if not isinstance(distribution, dict):
        raise LockError(f"{where}: missing distribution object")
    templates = distribution.get("templates")
    if not isinstance(templates, dict):
        raise LockError(f"{where}: missing distribution.templates (lock schema_version {LOCK_SCHEMA_VERSION})")
    return {
        "asset": _require_str(templates, "asset", f"{where} distribution.templates"),
        "manifest_asset": _require_str(templates, "manifest_asset", f"{where} distribution.templates"),
    }


def check_lock_shape(repo_root: Path, kind: str) -> list[str]:
    """Structural problems with one provider lock, as messages.

    Checks the things that only became checkable once the lock is the single
    declaration: that it agrees with its own filename, with the envelope it
    vendored, and with the asset names it pinned.
    """
    problems: list[str] = []
    path = lock_path(repo_root, kind)
    where = str(path.relative_to(repo_root)) if path.is_relative_to(repo_root) else str(path)

    try:
        lock = load_lock(path)
    except LockError as exc:
        return [str(exc)]

    version = lock.get("schema_version")
    if version != LOCK_SCHEMA_VERSION:
        problems.append(f"{where}: schema_version is {version!r}, expected {LOCK_SCHEMA_VERSION}")

    if lock.get("kind") != kind:
        problems.append(f"{where}: kind is {lock.get('kind')!r} but the filename says {kind!r}")

    env_path = envelope_path(repo_root, kind)
    if not env_path.is_file():
        problems.append(f"{where}: no vendored envelope at {env_path.name} — the lock records a kind nothing offers")
        return problems

    try:
        envelope = load_envelope(env_path)
    except LockError as exc:
        return [*problems, str(exc)]

    locked_version = lock.get("provider_version")
    envelope_version = envelope.get("provider_version")
    if not isinstance(locked_version, str) or not locked_version.strip():
        problems.append(f"{where}: missing non-empty string 'provider_version'")
    elif locked_version != envelope_version:
        # If these drift, #283's skew warning reports a version the vendored
        # envelope did not come from.
        problems.append(
            f"{where}: provider_version is {locked_version!r} but the vendored envelope says {envelope_version!r}"
        )

    try:
        templates = read_templates(lock, where)
    except LockError as exc:
        return [*problems, str(exc)]

    distribution = lock["distribution"]
    release = distribution.get("release")
    if not isinstance(release, dict):
        return [*problems, f"{where}: missing distribution.release object"]

    for key in ("repo", "tag"):
        # Without these a re-sync silently falls back to the anolis defaults and
        # fetches from the wrong org.
        try:
            _require_str(release, key, f"{where} distribution.release")
        except LockError as exc:
            problems.append(str(exc))

    tag = release.get("tag")
    if not isinstance(tag, str) or not tag.strip():
        return problems

    # Asset names are built from the TAG, both here and in install.sh, so the
    # tag is the version a machine pins. #283 compares that pin against the
    # envelope's provider_version — if the two disagree, every machine on this
    # provider gets a skew warning it can never clear.
    tag_version = version_of_tag(tag)
    if isinstance(locked_version, str) and locked_version.strip() and tag_version != locked_version:
        problems.append(
            f"{where}: release tag {tag!r} implies version {tag_version!r} but provider_version "
            f"is {locked_version!r}; a machine pinning the released version would warn about skew forever"
        )

    for key in ("asset", "manifest_asset"):
        try:
            rendered = render_template(templates[key], tag_version)
        except LockError as exc:
            problems.append(f"{where} distribution.templates.{key}: {exc}")
            continue
        pinned = release.get(key)
        if rendered != pinned:
            # Self-verifying: a template that cannot reproduce the asset it
            # pinned is a second, disagreeing copy rather than a record.
            problems.append(
                f"{where}: distribution.templates.{key} renders {rendered!r} at "
                f"version {tag_version} but release.{key} is {pinned!r}"
            )

    return problems


def check_registry(repo_root: Path) -> list[str]:
    """Problems across the whole provider registry.

    The load-bearing one is the 1:1 invariant: a lock without an envelope is a
    kind nothing offers, and an envelope without a lock is a kind whose
    provenance nothing records — the state #285 exists to make impossible.
    """
    problems: list[str] = []
    locked_ok, locked_bad = _stems(repo_root / LOCK_DIR, _LOCK_SUFFIX)
    vendored_ok, vendored_bad = _stems(repo_root / ENVELOPE_DIR, _ENVELOPE_SUFFIX)

    for name in locked_bad:
        problems.append(f"lock filename '{name}{_LOCK_SUFFIX}' is not a legal provider kind ({_KIND_RE.pattern})")
    for name in vendored_bad:
        problems.append(
            f"envelope filename '{name}{_ENVELOPE_SUFFIX}' is not a legal provider kind ({_KIND_RE.pattern})"
        )

    locked = set(locked_ok)
    vendored = set(vendored_ok)

    for kind in sorted(locked - vendored):
        problems.append(f"provider '{kind}' has a lock but no vendored envelope")
    for kind in sorted(vendored - locked):
        problems.append(f"provider '{kind}' has a vendored envelope but no lock — its provenance is unrecorded")

    for kind in sorted(locked & vendored):
        problems.extend(check_lock_shape(repo_root, kind))
    return problems
