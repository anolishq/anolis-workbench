#!/usr/bin/env python3
"""Sync vendored upstream schemas from an anolis release artifact.

This script downloads a schema release bundle from:
  https://github.com/anolishq/anolis/releases/download/<tag>/<asset>

Then it:
1. Extracts the schema file from the bundle.
2. Verifies the bundle SHA256 against the release manifest.
3. Updates the vendored schema copy in anolis_workbench/schemas/.
4. Rewrites the lock file in release-artifact mode with pinned checksums.

Supported schemas:
  --schema runtime-config    -> anolis-{VERSION}-runtime-config-schema.tar.gz
  --schema machine-profile   -> anolis-{VERSION}-machine-profile-schema.tar.gz

Usage:
  python scripts/sync-upstream-schema-from-release.py --schema runtime-config --tag v0.1.3
  python scripts/sync-upstream-schema-from-release.py --schema machine-profile --tag v0.1.3
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

_REPO_ROOT = Path(__file__).resolve().parents[1]

_SCHEMA_CONFIGS: dict[str, dict[str, str]] = {
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
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_url_bytes(url: str, timeout_seconds: int = 45) -> bytes:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            return response.read()
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync a vendored upstream schema from an anolis release artifact"
    )
    parser.add_argument(
        "--schema",
        required=True,
        choices=list(_SCHEMA_CONFIGS.keys()),
        help="Which schema to sync",
    )
    parser.add_argument(
        "--tag",
        required=True,
        help="Upstream release tag (e.g. v0.1.3)",
    )
    parser.add_argument(
        "--upstream-repo",
        default="anolishq/anolis",
        help="Upstream GitHub repo in owner/name form",
    )
    parser.add_argument(
        "--repo-root",
        default=str(_REPO_ROOT),
        help="Repository root path (default: auto-detected)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = _SCHEMA_CONFIGS[args.schema]

    repo_root = Path(args.repo_root).resolve()
    schema_out = (repo_root / cfg["vendored_path"]).resolve()
    lock_out = (repo_root / cfg["lock_path"]).resolve()

    version = args.tag[1:] if args.tag.startswith("v") else args.tag
    asset = cfg["asset_template"].format(version=version)
    manifest_asset = cfg["manifest_asset"]

    asset_url = f"https://github.com/{args.upstream_repo}/releases/download/{args.tag}/{asset}"
    manifest_url = f"https://github.com/{args.upstream_repo}/releases/download/{args.tag}/{manifest_asset}"

    print(f"Fetching {asset_url} ...")
    asset_bytes = fetch_url_bytes(asset_url)
    asset_sha = sha256_bytes(asset_bytes)

    schema_bytes = extract_tar_member(asset_bytes, cfg["schema_member"])
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
        raise RuntimeError(
            f"schema manifest asset mismatch: expected '{asset}', found '{manifest_asset_name}'"
        )
    if manifest_sha != asset_sha:
        raise RuntimeError(
            f"schema manifest sha256 mismatch: expected '{asset_sha}', found '{manifest_sha}'"
        )

    schema_out.parent.mkdir(parents=True, exist_ok=True)
    lock_out.parent.mkdir(parents=True, exist_ok=True)

    schema_out.write_bytes(schema_bytes)

    lock_payload = {
        "schema_version": 2,
        "source": {
            "repo": args.upstream_repo,
            "path": cfg["schema_member"],
            "tag": args.tag,
        },
        "distribution": {
            "mode": "release-artifact",
            "release": {
                "repo": args.upstream_repo,
                "tag": args.tag,
                "asset": asset,
                "manifest_asset": manifest_asset,
            },
            "schema_sha256": schema_sha,
            "asset_sha256": asset_sha,
        },
        "synced_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }

    lock_out.write_text(json.dumps(lock_payload, indent=2) + "\n", encoding="utf-8")

    print("\nUpstream schema sync summary")
    print(f"  schema:          {args.schema}")
    print(f"  repo:            {args.upstream_repo}")
    print(f"  tag:             {args.tag}")
    print(f"  asset:           {asset}")
    print(f"  asset_sha256:    {asset_sha}")
    print(f"  schema_member:   {cfg['schema_member']}")
    print(f"  schema_sha256:   {schema_sha}")
    print(f"  schema_out:      {schema_out}")
    print(f"  lock_out:        {lock_out}")
    print("\nSync complete. Commit updated schema + lock file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
