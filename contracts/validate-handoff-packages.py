#!/usr/bin/env python3
"""Validate an Anolis handoff package (.anpkg or extracted directory)."""

from __future__ import annotations

import argparse
import pathlib
import sys

# Allow running this script directly from contracts/ without requiring installation.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anolis_workbench.core import package_validator


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an Anolis handoff package archive or extracted directory.",
    )
    parser.add_argument(
        "package_path",
        help="Path to .anpkg/.zip archive or extracted package directory.",
    )
    parser.add_argument(
        "--runtime-bin",
        default=None,
        help="Optional runtime binary path for --check-config replay validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    package_path = pathlib.Path(args.package_path).expanduser().resolve()
    runtime_bin = pathlib.Path(args.runtime_bin).expanduser().resolve() if args.runtime_bin else None

    try:
        package_validator.validate_package(package_path, runtime_bin=runtime_bin)
    except package_validator.PackageValidationError as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Package validation passed: {package_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
