#!/usr/bin/env python3
"""Generate Tauri updater manifest (latest.json) for a release.

Usage:
    python scripts/generate-update-manifest.py --version 0.11.0 --output latest.json

The manifest tells Tauri where to download the update and what signature to
verify. Signature fields are left empty until a signing key is provisioned.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

REPO = "anolishq/anolis-workbench"
GITHUB_RELEASES = f"https://github.com/{REPO}/releases/download"


def build_manifest(version: str, notes: str = "") -> dict:
    """Build the Tauri updater manifest structure."""
    tag = f"v{version}"
    pub_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "version": version,
        "notes": notes or f"Release {version}. See CHANGELOG for details.",
        "pub_date": pub_date,
        "platforms": {
            "linux-x86_64": {
                "url": f"{GITHUB_RELEASES}/{tag}/anolis-workbench-{version}-amd64.AppImage.tar.gz",
                "signature": "",
            },
            "linux-aarch64": {
                "url": f"{GITHUB_RELEASES}/{tag}/anolis-workbench-{version}-arm64.AppImage.tar.gz",
                "signature": "",
            },
            "darwin-aarch64": {
                "url": f"{GITHUB_RELEASES}/{tag}/anolis-workbench-{version}-aarch64.app.tar.gz",
                "signature": "",
            },
            "darwin-x86_64": {
                "url": f"{GITHUB_RELEASES}/{tag}/anolis-workbench-{version}-x86_64.app.tar.gz",
                "signature": "",
            },
            "windows-x86_64": {
                "url": f"{GITHUB_RELEASES}/{tag}/anolis-workbench-{version}-x64.msi.zip",
                "signature": "",
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Tauri updater manifest.")
    parser.add_argument("--version", required=True, help="Release version (e.g. 0.11.0)")
    parser.add_argument("--notes", default="", help="Release notes text")
    parser.add_argument("--output", default="latest.json", help="Output file path")
    args = parser.parse_args()

    manifest = build_manifest(args.version, args.notes)

    with open(args.output, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    print(f"[manifest] wrote {args.output} for v{args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
