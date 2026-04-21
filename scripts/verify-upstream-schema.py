#!/usr/bin/env python3
"""Verify vendored upstream schema lock state.

Validates that a vendored schema copy in anolis_workbench/schemas/ matches
the corresponding lock file, and optionally verifies against the release artifact.

Modes (from lock file):
  release-artifact: verifies vendored schema SHA and release artifact parity.

Supported schemas:
  --schema runtime-config
  --schema machine-profile

Usage:
  python scripts/verify-upstream-schema.py --schema runtime-config
  python scripts/verify-upstream-schema.py --schema machine-profile --offline
  python scripts/verify-upstream-schema.py --schema runtime-config --require-release-artifact
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

_REPO_ROOT = Path(__file__).resolve().parents[1]

_SCHEMA_CONFIGS: dict[str, dict[str, str]] = {
    "runtime-config": {
        "schema_path": "anolis_workbench/schemas/runtime-config.schema.json",
        "lock_path": "contracts/upstream/anolis/runtime-config.lock.json",
    },
    "machine-profile": {
        "schema_path": "anolis_workbench/schemas/machine-profile.schema.json",
        "lock_path": "contracts/upstream/anolis/machine-profile.lock.json",
    },
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_url_bytes(url: str, timeout_seconds: int = 30) -> bytes:
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


def load_lock(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to parse lock file: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("lock file root must be a JSON object")
    return raw


def validate_release_mode(
    schema_bytes: bytes,
    schema_path: Path,
    lock_path: Path,
    lock: dict[str, Any],
    *,
    offline: bool,
) -> int:
    distribution = lock.get("distribution")
    source = lock.get("source")

    if not isinstance(distribution, dict):
        print("ERROR: lock file missing distribution object", file=sys.stderr)
        return 1
    if not isinstance(source, dict):
        print("ERROR: lock file missing source object", file=sys.stderr)
        return 1

    expected_schema_sha = distribution.get("schema_sha256")
    expected_asset_sha = distribution.get("asset_sha256")
    release = distribution.get("release")

    if not isinstance(expected_schema_sha, str) or len(expected_schema_sha) != 64:
        print("ERROR: lock file missing valid distribution.schema_sha256", file=sys.stderr)
        return 1
    if not isinstance(expected_asset_sha, str) or len(expected_asset_sha) != 64:
        print("ERROR: lock file missing valid distribution.asset_sha256", file=sys.stderr)
        return 1
    if not isinstance(release, dict):
        print("ERROR: lock file missing distribution.release object", file=sys.stderr)
        return 1

    repo = release.get("repo")
    tag = release.get("tag")
    asset = release.get("asset")
    manifest_asset = release.get("manifest_asset")
    source_path = source.get("path")

    if not all(isinstance(v, str) and v.strip() for v in (repo, tag, asset, source_path)):
        print("ERROR: lock file missing required release fields (repo, tag, asset, source.path)", file=sys.stderr)
        return 1

    local_schema_sha = sha256_bytes(schema_bytes)
    print("upstream schema verification summary (release-artifact mode)")
    print(f"  schema:             {schema_path}")
    print(f"  lock:               {lock_path}")
    print(f"  release repo/tag:   {repo}@{tag}")
    print(f"  release asset:      {asset}")
    print(f"  expected schema:    {expected_schema_sha}")
    print(f"  local schema:       {local_schema_sha}")

    if local_schema_sha != expected_schema_sha:
        print("\nERROR: local schema checksum mismatch", file=sys.stderr)
        return 1

    if offline:
        print("\nRelease artifact fetch skipped (--offline).")
        print("Schema checksum verification passed (local-only).")
        return 0

    asset_url = f"https://github.com/{repo}/releases/download/{tag}/{asset}"
    try:
        asset_bytes = fetch_url_bytes(asset_url)
    except RuntimeError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    fetched_asset_sha = sha256_bytes(asset_bytes)
    print(f"  expected asset:     {expected_asset_sha}")
    print(f"  fetched asset:      {fetched_asset_sha}")

    if fetched_asset_sha != expected_asset_sha:
        print("\nERROR: release artifact checksum mismatch", file=sys.stderr)
        return 1

    try:
        extracted_schema = extract_tar_member(asset_bytes, source_path)
    except RuntimeError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    extracted_schema_sha = sha256_bytes(extracted_schema)
    print(f"  extracted schema:   {extracted_schema_sha}")

    if extracted_schema_sha != expected_schema_sha:
        print("\nERROR: schema checksum in artifact mismatch", file=sys.stderr)
        return 1

    if extracted_schema != schema_bytes:
        print("\nERROR: vendored schema does not match release artifact schema payload", file=sys.stderr)
        return 1

    if isinstance(manifest_asset, str) and manifest_asset.strip():
        manifest_url = f"https://github.com/{repo}/releases/download/{tag}/{manifest_asset}"
        try:
            manifest_bytes = fetch_url_bytes(manifest_url)
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            if isinstance(manifest, dict):
                manifest_asset_name = manifest.get("asset")
                manifest_sha = manifest.get("sha256")
                if manifest_asset_name != asset or manifest_sha != expected_asset_sha:
                    print("\nERROR: schema manifest does not match pinned lock values", file=sys.stderr)
                    return 1
        except Exception as exc:
            print(f"\nERROR: failed to validate manifest asset '{manifest_asset}': {exc}", file=sys.stderr)
            return 1

    print("\nRelease artifact and schema lock verification passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify vendored upstream schema against lock metadata."
    )
    parser.add_argument(
        "--schema",
        required=True,
        choices=list(_SCHEMA_CONFIGS.keys()),
        help="Which schema to verify",
    )
    parser.add_argument(
        "--repo-root",
        default=str(_REPO_ROOT),
        help="Repository root path (default: auto-detected)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip network fetch; validate local lock/schema only",
    )
    parser.add_argument(
        "--require-release-artifact",
        action="store_true",
        help="Fail if lock mode is not release-artifact",
    )
    args = parser.parse_args()

    cfg = _SCHEMA_CONFIGS[args.schema]
    repo_root = Path(args.repo_root).resolve()
    schema_path = (repo_root / cfg["schema_path"]).resolve()
    lock_path = (repo_root / cfg["lock_path"]).resolve()

    if not schema_path.is_file():
        print(f"ERROR: schema file not found: {schema_path}", file=sys.stderr)
        return 1
    if not lock_path.is_file():
        print(f"ERROR: lock file not found: {lock_path}", file=sys.stderr)
        return 1

    try:
        lock = load_lock(lock_path)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    schema_bytes = schema_path.read_bytes()

    distribution = lock.get("distribution") if isinstance(lock, dict) else None
    mode = distribution.get("mode") if isinstance(distribution, dict) else None

    if args.require_release_artifact and mode != "release-artifact":
        print(
            f"ERROR: lock mode is '{mode}', but --require-release-artifact requires 'release-artifact'",
            file=sys.stderr,
        )
        return 1

    if mode == "release-artifact":
        return validate_release_mode(
            schema_bytes, schema_path, lock_path, lock, offline=args.offline
        )

    print(f"ERROR: unsupported lock mode '{mode}'", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
