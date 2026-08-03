#!/usr/bin/env python3
"""Sync vendored upstream schemas from an upstream release artifact.

This script downloads a schema release asset from:
  https://github.com/<upstream-repo>/releases/download/<tag>/<asset>

Then it:
1. Obtains the schema document (extracted from a tar bundle, or the raw asset
   itself for provider config-schema envelopes).
2. Verifies the asset SHA256 against the release manifest sidecar (and, for
   provider envelopes, the executable-profile v1 §2 envelope shape).
3. Updates the vendored schema copy under anolis_workbench/schemas/.
4. Rewrites the lock file in release-artifact mode with pinned checksums.

Anolis schemas come from anolishq/anolis and are a fixed set of three, declared
below. Provider config-schema envelopes come from each provider's own repo and
are NOT declared here (#285) — the lock files under
`contracts/upstream/providers/` are the single declaration, and this script
discovers them. That is what makes adding a provider a data change rather than
a code change, and it breaks the old circularity where `--schema` was
`choices=list(_SCHEMA_CONFIGS.keys())`, so a kind had to be in the dict before
it could be synced.

Supported schemas:
  --schema runtime-config    -> anolis-{VERSION}-runtime-config-schema.tar.gz
  --schema machine-profile   -> anolis-{VERSION}-machine-profile-schema.tar.gz
  --schema runtime-http      -> anolis-{VERSION}-runtime-http-schema.tar.gz
  --schema provider-config-<kind>  (one per lock in contracts/upstream/providers/)

Usage:
  python scripts/sync-upstream-schema-from-release.py --schema runtime-config --tag v0.1.3
  python scripts/sync-upstream-schema-from-release.py --schema provider-config-sim --tag v0.2.8

  # Onboard a provider that has no lock yet — no code change required:
  python scripts/sync-upstream-schema-from-release.py \
      --new-provider foo --repo vendor/anolis-provider-foo --tag v0.1.0
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import provider_locks

_REPO_ROOT = Path(__file__).resolve().parents[1]

_PROVIDER_PREFIX = "provider-config-"

# The anolis-side schemas: a fixed set of three, not a registry. They come from
# one repo, are tar members rather than raw assets, and gain no entries when a
# provider is added — so a dict is the honest shape here.
_ANOLIS_CONFIGS: dict[str, dict[str, str]] = {
    "runtime-config": {
        "asset_template": "anolis-{version}-runtime-config-schema.tar.gz",
        "manifest_asset": "runtime-config-schema-manifest.json",
        "schema_member": "schemas/runtime/runtime-config.schema.json",
        "vendored_path": "anolis_workbench/schemas/runtime-config.schema.json",
        "lock_path": "contracts/upstream/anolis/runtime-config.lock.json",
    },
    "machine-profile": {
        "asset_template": "anolis-{version}-machine-profile-schema.tar.gz",
        "manifest_asset": "machine-profile-schema-manifest.json",
        "schema_member": "schemas/machine/machine-profile.schema.json",
        "vendored_path": "anolis_workbench/schemas/machine-profile.schema.json",
        "lock_path": "contracts/upstream/anolis/machine-profile.lock.json",
    },
    "runtime-http": {
        "asset_template": "anolis-{version}-runtime-http-schema.tar.gz",
        "manifest_asset": "runtime-http-schema-manifest.json",
        "schema_member": "schemas/http/runtime-http.openapi.v0.yaml",
        "vendored_path": "contracts/runtime-http.openapi.v0.yaml",
        "lock_path": "contracts/upstream/anolis/runtime-http.lock.json",
    },
}


def _provider_plan(repo_root: Path, kind: str, *, repo: str | None, templates: dict[str, str]) -> dict[str, Any]:
    """A sync plan for one provider kind, in the same shape as an anolis entry.

    Provider envelopes are raw assets (the asset IS the schema document) and
    carry the executable-profile v1 §2 envelope shape.
    """
    lock_file = provider_locks.lock_path(repo_root, kind)
    return {
        "kind": kind,
        "default_repo": repo,
        "asset_template": templates["asset"],
        "manifest_asset_template": templates["manifest_asset"],
        "raw_asset": True,
        "envelope": True,
        "vendored_path": str(provider_locks.envelope_path(repo_root, kind).relative_to(repo_root)),
        "lock_path": str(lock_file.relative_to(repo_root)),
    }


def _plan_for_existing_provider(repo_root: Path, kind: str) -> dict[str, Any]:
    """Re-sync: repo and asset templates come back out of the lock, so a vendor
    whose naming differs from the convention edits the lock once, not this file."""
    path = provider_locks.lock_path(repo_root, kind)
    lock = provider_locks.load_lock(path)
    where = path.name
    templates = provider_locks.read_templates(lock, where)
    release = lock.get("distribution", {}).get("release")
    repo = release.get("repo") if isinstance(release, dict) else None
    if not isinstance(repo, str) or not repo.strip():
        # Falling through to the anolis default here would fetch a provider
        # asset from the wrong org and 404 with a confusing URL.
        raise provider_locks.LockError(f"{where}: missing distribution.release.repo; cannot tell where to re-sync from")
    return _provider_plan(repo_root, kind, repo=repo, templates=templates)


def validate_envelope_shape(schema_bytes: bytes) -> None:
    """Provider envelopes must satisfy the executable profile v1 §2 shape."""
    try:
        doc = json.loads(schema_bytes.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"provider envelope is not parseable JSON: {exc}") from exc
    version = doc.get("config_schema_version") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise RuntimeError("provider envelope must carry an integer config_schema_version >= 1")
    if not isinstance(doc.get("schema"), dict):
        raise RuntimeError("provider envelope 'schema' must be a JSON object")
    # Without it the workbench cannot tell whether a machine's pin agrees with
    # the contract it is validating against (#283) — and would say nothing.
    provider_version = doc.get("provider_version")
    if not isinstance(provider_version, str) or not provider_version.strip():
        raise RuntimeError("provider envelope must carry a non-empty string provider_version")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_url_bytes(url: str, timeout_seconds: int = 45) -> bytes:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            return bytes(response.read())
    except HTTPError as exc:
        raise RuntimeError(f"HTTP error while fetching {url}: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while fetching {url}: {exc.reason}") from exc


def extract_tar_member(archive_bytes: bytes, member_path: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        try:
            member = archive.getmember(member_path)
        except KeyError:
            try:
                member = archive.getmember(f"./{member_path}")
            except KeyError as exc:
                raise RuntimeError(f"schema member not found in artifact: {member_path}") from exc

        if not member.isfile():
            raise RuntimeError(f"schema member is not a regular file: {member_path}")

        handle = archive.extractfile(member)
        if handle is None:
            raise RuntimeError(f"failed to extract schema member: {member_path}")
        return handle.read()


def known_schemas(repo_root: Path) -> list[str]:
    """Data-derived, not hardcoded: every provider lock contributes a name, so a
    kind becomes syncable by existing rather than by being listed in code."""
    return sorted(_ANOLIS_CONFIGS) + [f"{_PROVIDER_PREFIX}{k}" for k in provider_locks.locked_kinds(repo_root)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync a vendored upstream schema from an anolis release artifact")
    # NB: --schema is validated after parsing, against the RESOLVED --repo-root.
    # argparse `choices` would have to be computed before --repo-root is known,
    # which silently validates against the wrong tree.
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--schema", metavar="NAME", help="Which already-locked schema to sync")
    target.add_argument(
        "--new-provider",
        metavar="KIND",
        help="Onboard a provider that has no lock yet (requires --repo)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Provider repo in owner/name form; required with --new-provider",
    )
    parser.add_argument(
        "--asset-template",
        default=None,
        help="Override the config-schema asset name pattern, e.g. 'foo-{version}-schema.json'",
    )
    parser.add_argument(
        "--manifest-asset-template",
        default=None,
        help="Override the manifest sidecar name pattern",
    )
    parser.add_argument(
        "--tag",
        required=True,
        help="Upstream release tag (e.g. v0.1.3)",
    )
    parser.add_argument(
        "--upstream-repo",
        default=None,
        help="Upstream GitHub repo in owner/name form (default: the schema entry's repo)",
    )
    parser.add_argument(
        "--repo-root",
        default=str(_REPO_ROOT),
        help="Repository root path (default: auto-detected)",
    )
    args = parser.parse_args()
    if args.new_provider and not args.repo:
        parser.error("--new-provider requires --repo (there is no lock to read it from yet)")
    if args.repo and not args.new_provider:
        parser.error("--repo applies to --new-provider; use --upstream-repo to override an existing lock")
    if args.repo and args.upstream_repo:
        parser.error("--repo and --upstream-repo both name the source repo; pass only one")
    if args.schema is not None:
        known = known_schemas(Path(args.repo_root).resolve())
        if args.schema not in known:
            parser.error(f"argument --schema: invalid choice: {args.schema!r} (choose from {', '.join(known)})")
    return args


def _resolve_plan(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    if args.new_provider:
        kind = provider_locks.validate_kind(args.new_provider)
        if provider_locks.lock_path(repo_root, kind).is_file():
            raise RuntimeError(
                f"provider '{kind}' already has a lock; re-sync it with --schema {_PROVIDER_PREFIX}{kind}"
            )
        templates = provider_locks.asset_templates(kind)
        if args.asset_template:
            templates["asset"] = args.asset_template
        if args.manifest_asset_template:
            templates["manifest_asset"] = args.manifest_asset_template
        return _provider_plan(repo_root, kind, repo=args.repo, templates=templates)

    if args.asset_template or args.manifest_asset_template:
        raise RuntimeError("asset name templates live in the lock for an existing provider; edit the lock instead")
    if args.schema.startswith(_PROVIDER_PREFIX):
        return _plan_for_existing_provider(repo_root, args.schema[len(_PROVIDER_PREFIX) :])
    return dict(_ANOLIS_CONFIGS[args.schema])


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    try:
        cfg = _resolve_plan(args, repo_root)
    except (RuntimeError, provider_locks.LockError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    schema_out = (repo_root / cfg["vendored_path"]).resolve()
    lock_out = (repo_root / cfg["lock_path"]).resolve()

    upstream_repo = args.upstream_repo or cfg.get("default_repo") or "anolishq/anolis"

    version = provider_locks.version_of_tag(args.tag)
    try:
        asset = provider_locks.render_template(cfg["asset_template"], version)
        if "manifest_asset_template" in cfg:
            manifest_asset = provider_locks.render_template(cfg["manifest_asset_template"], version)
        else:
            manifest_asset = cfg["manifest_asset"]
    except provider_locks.LockError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    asset_url = f"https://github.com/{upstream_repo}/releases/download/{args.tag}/{asset}"
    manifest_url = f"https://github.com/{upstream_repo}/releases/download/{args.tag}/{manifest_asset}"

    print(f"Fetching {asset_url} ...")
    asset_bytes = fetch_url_bytes(asset_url)
    asset_sha = sha256_bytes(asset_bytes)

    if cfg.get("raw_asset"):
        # The asset IS the schema document (provider config-schema envelopes).
        schema_bytes = asset_bytes
    else:
        schema_bytes = extract_tar_member(asset_bytes, cfg["schema_member"])
    envelope_version = ""
    if cfg.get("envelope"):
        validate_envelope_shape(schema_bytes)
        envelope_version = json.loads(schema_bytes.decode("utf-8"))["provider_version"]
        if envelope_version != version:
            # The asset name is built from the tag, and install.sh derives
            # provider asset names the same way — so the tag version is what a
            # machine PINS. If the envelope declares something else, #283 would
            # warn about skew on every machine using this provider, forever.
            # Checked before anything is written: bailing out after the
            # envelope lands would leave an envelope with no lock, which is a
            # CI failure in its own right.
            print(
                f"ERROR: release tag {args.tag} implies version {version!r} but the envelope declares "
                f"provider_version {envelope_version!r}. Refusing to write a lock that disagrees with itself.",
                file=sys.stderr,
            )
            return 1
    schema_sha = sha256_bytes(schema_bytes)

    print(f"Fetching {manifest_url} ...")
    manifest_bytes = fetch_url_bytes(manifest_url)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to parse schema manifest asset as JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise RuntimeError("schema manifest asset must be a JSON object")

    manifest_asset_name = manifest.get("asset")
    manifest_sha = manifest.get("sha256")
    if manifest_asset_name != asset:
        raise RuntimeError(f"schema manifest asset mismatch: expected '{asset}', found '{manifest_asset_name}'")
    if manifest_sha != asset_sha:
        raise RuntimeError(f"schema manifest sha256 mismatch: expected '{asset_sha}', found '{manifest_sha}'")

    schema_out.parent.mkdir(parents=True, exist_ok=True)
    lock_out.parent.mkdir(parents=True, exist_ok=True)

    schema_out.write_bytes(schema_bytes)

    distribution: dict[str, Any] = {
        "mode": "release-artifact",
        "asset_format": "raw" if cfg.get("raw_asset") else "tar-member",
    }
    lock_payload: dict[str, Any] = {"schema_version": 2}

    if cfg.get("kind"):
        # Provider locks are the registry (#285): they carry the kind, the
        # version the envelope came from, and the templates a re-sync reads
        # back. provider_version is taken from the envelope we just fetched and
        # validated, so the lock cannot disagree with it by construction.
        lock_payload["schema_version"] = provider_locks.LOCK_SCHEMA_VERSION
        lock_payload["kind"] = cfg["kind"]
        lock_payload["provider_version"] = envelope_version
        distribution["templates"] = {
            "asset": cfg["asset_template"],
            "manifest_asset": cfg["manifest_asset_template"],
        }

    distribution["release"] = {
        "repo": upstream_repo,
        "tag": args.tag,
        "asset": asset,
        "manifest_asset": manifest_asset,
    }
    distribution["schema_sha256"] = schema_sha
    distribution["asset_sha256"] = asset_sha

    lock_payload["source"] = {
        "repo": upstream_repo,
        "path": asset if cfg.get("raw_asset") else cfg["schema_member"],
        "tag": args.tag,
    }
    lock_payload["distribution"] = distribution
    lock_payload["synced_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    lock_out.write_text(json.dumps(lock_payload, indent=2) + "\n", encoding="utf-8")

    label = args.schema or f"{_PROVIDER_PREFIX}{cfg['kind']} (new)"
    print("\nUpstream schema sync summary")
    print(f"  schema:          {label}")
    print(f"  repo:            {upstream_repo}")
    print(f"  tag:             {args.tag}")
    print(f"  asset:           {asset}")
    print(f"  asset_sha256:    {asset_sha}")
    print(f"  schema_member:   {cfg.get('schema_member', '(raw asset)')}")
    print(f"  schema_sha256:   {schema_sha}")
    print(f"  schema_out:      {schema_out}")
    print(f"  lock_out:        {lock_out}")
    print("\nSync complete. Commit updated schema + lock file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
