#!/usr/bin/env python3
"""Freeze anolis-workbench server entrypoint into a sidecar executable.

Release path:

1. Build SPA assets first (`frontend -> anolis_workbench/frontend/dist`).
2. Freeze the Python server with PyInstaller onefile mode.
3. Enforce minimum-size and smoke-test guards to detect incomplete bundles.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
from typing import Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_ENTRYPOINT = REPO_ROOT / "anolis_workbench" / "cli" / "main.py"
DEFAULT_FRONTEND_INDEX = REPO_ROOT / "anolis_workbench" / "frontend" / "dist" / "index.html"

# Runtime-loaded modules PyInstaller may miss without explicit hints.
#
# DERIVED from the tree rather than hand-listed. The previous static list had
# drifted badly: it named three modules that no longer exist (core.bundler,
# core.preflight, core.systemd) while omitting ten that do. Phantom entries only
# warn, but an OMISSION silently drops a module from the frozen sidecar and
# surfaces as an ImportError at runtime, on a user's machine, long after the
# build looked clean. A list that mirrors the filesystem should be read from the
# filesystem.
_HIDDEN_IMPORT_PACKAGES: tuple[str, ...] = (
    "anolis_workbench.server.routes",
    "anolis_workbench.core",
)


def _discover_hidden_imports() -> list[str]:
    """Every non-dunder module in the packages loaded dynamically at runtime."""
    found: list[str] = []
    for package in _HIDDEN_IMPORT_PACKAGES:
        package_dir = REPO_ROOT / pathlib.Path(*package.split("."))
        if not package_dir.is_dir():
            raise SystemExit(f"freeze_server: expected package directory not found: {package_dir}")
        for module in sorted(package_dir.glob("*.py")):
            if module.stem.startswith("__"):
                continue
            found.append(f"{package}.{module.stem}")
    return found


DEFAULT_HIDDEN_IMPORTS = _discover_hidden_imports()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze anolis-workbench sidecar executable with validation guards.",
    )
    parser.add_argument(
        "--name",
        default="anolis-workbench",
        help="Executable base name (default: anolis-workbench).",
    )
    parser.add_argument(
        "--entrypoint",
        default=str(DEFAULT_ENTRYPOINT),
        help="Python entrypoint file to freeze (default: anolis_workbench/cli/main.py).",
    )
    parser.add_argument(
        "--output-dir",
        default="dist",
        help="Output directory for frozen executable (default: dist).",
    )
    parser.add_argument(
        "--work-dir",
        default="build/pyinstaller",
        help="PyInstaller work path (default: build/pyinstaller).",
    )
    parser.add_argument(
        "--spec-dir",
        default="build/pyinstaller-spec",
        help="PyInstaller spec output directory (default: build/pyinstaller-spec).",
    )
    parser.add_argument(
        "--min-size-bytes",
        type=int,
        default=10_000_000,
        help="Fail if executable size is smaller than this threshold (default: 10000000).",
    )
    parser.add_argument(
        "--smoke-timeout-seconds",
        type=int,
        default=10,
        help="Timeout (seconds) for executable smoke checks (default: 10).",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip executable smoke checks (not recommended for CI/release).",
    )
    parser.add_argument(
        "--hidden-import",
        action="append",
        default=[],
        help="Additional hidden imports for PyInstaller (repeatable).",
    )
    return parser.parse_args()


def _require_frontend_bundle() -> None:
    if DEFAULT_FRONTEND_INDEX.is_file():
        return
    raise SystemExit(
        f"Missing frontend bundle at {DEFAULT_FRONTEND_INDEX}. Run `npm run build` in frontend/ before freezing."
    )


def _build_pyinstaller_command(args: argparse.Namespace, hidden_imports: Sequence[str]) -> list[str]:
    entrypoint = pathlib.Path(args.entrypoint)
    if not entrypoint.is_absolute():
        entrypoint = (REPO_ROOT / entrypoint).resolve()
    if not entrypoint.is_file():
        raise SystemExit(f"Entrypoint not found: {entrypoint}")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        args.name,
        "--distpath",
        args.output_dir,
        "--workpath",
        args.work_dir,
        "--specpath",
        args.spec_dir,
        "--collect-all",
        "anolis_workbench",
    ]

    for module_name in hidden_imports:
        cmd.extend(["--hidden-import", module_name])

    cmd.append(str(entrypoint))
    return cmd


def _resolve_output_executable(name: str, output_dir: str) -> pathlib.Path:
    suffix = ".exe" if os.name == "nt" else ""
    return (REPO_ROOT / output_dir / f"{name}{suffix}").resolve()


def _run_smoke_checks(executable: pathlib.Path, timeout_seconds: int) -> None:
    # --help and --version are non-blocking checks that validate imports and CLI wiring.
    for args in (["--help"], ["--version"]):
        subprocess.run(
            [str(executable), *args],
            cwd=str(REPO_ROOT),
            check=True,
            timeout=timeout_seconds,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=False,
        )


def main() -> int:
    args = _parse_args()
    _require_frontend_bundle()

    hidden_imports = [*DEFAULT_HIDDEN_IMPORTS, *args.hidden_import]
    command = _build_pyinstaller_command(args, hidden_imports)

    print("[freeze] running:", " ".join(command))
    subprocess.run(command, cwd=str(REPO_ROOT), check=True)

    executable = _resolve_output_executable(args.name, args.output_dir)
    if not executable.is_file():
        raise SystemExit(f"Frozen executable not found at {executable}")

    size_bytes = executable.stat().st_size
    if size_bytes < args.min_size_bytes:
        raise SystemExit(f"Frozen executable is too small ({size_bytes} bytes < {args.min_size_bytes} bytes).")

    if not args.skip_smoke:
        _run_smoke_checks(executable, timeout_seconds=args.smoke_timeout_seconds)

    print(f"[freeze] executable: {executable}")
    print(f"[freeze] size_bytes: {size_bytes}")
    print("[freeze] smoke_checks: skipped" if args.skip_smoke else "[freeze] smoke_checks: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
