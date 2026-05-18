"""CLI entry point for `anolis-provision` — local provisioning of Anolis components."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from anolis_workbench.core import installer


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="anolis-provision",
        description="Provision Anolis runtime and providers on this machine.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Provisioning mode")

    # --- install subcommand (pass 3) ---
    install_parser = subparsers.add_parser(
        "install",
        help="Install binaries and create a workbench project locally.",
    )
    install_parser.add_argument(
        "--project",
        default="bioreactor-v1",
        help="Project name to create (default: bioreactor-v1).",
    )
    install_parser.add_argument(
        "--template",
        default="bioreactor-manual",
        help="Template to use for project creation (default: bioreactor-manual).",
    )
    install_parser.add_argument(
        "--install-prefix",
        type=Path,
        default=Path("/usr/local"),
        help="Binary install prefix (default: /usr/local).",
    )
    install_parser.add_argument(
        "--compat-matrix",
        type=Path,
        default=None,
        help="Override path to compatibility-matrix.yaml (for testing).",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing binaries and project.",
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and verify but do not install or write files.",
    )

    return parser.parse_args()


def _print_progress(step: str, detail: str = "") -> None:
    """Print structured progress to stdout."""
    prefix_map = {
        "compat": "📋",
        "resolve": "🔍",
        "platform": "🖥️ ",
        "manifest": "📦",
        "download": "⬇️ ",
        "verified": "✓ ",
        "skip": "⏭️ ",
        "skip_all": "✓ ",
        "install": "📥",
        "verify": "🔎",
        "project": "📁",
        "dry_run": "🔍",
        "done": "✅",
    }
    icon = prefix_map.get(step, "  ")
    msg = f"  {icon} {detail}" if detail else f"  {icon} {step}"
    print(msg)


def _run_install(args: argparse.Namespace) -> int:
    """Execute the install subcommand."""
    import os

    token = os.environ.get("GITHUB_TOKEN")

    print("Anolis Provision — Install")
    print(f"  Project:  {args.project}")
    print(f"  Template: {args.template}")
    print(f"  Prefix:   {args.install_prefix}")
    if args.dry_run:
        print("  Mode:     DRY RUN")
    print()

    try:
        result = installer.install(
            project_name=args.project,
            template_name=args.template,
            install_prefix=args.install_prefix,
            compat_matrix_path=args.compat_matrix,
            github_token=token,
            force=args.force,
            dry_run=args.dry_run,
            progress_callback=_print_progress,
        )
    except installer.InstallerError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    # Print summary
    print()
    print("─" * 60)
    if result.dry_run:
        print("DRY RUN complete — no changes were made.")
    else:
        print("Installation complete!")
        print()
        for binary, version in result.verified_versions.items():
            print(f"  ✓ {binary:<30} {version}")
        print()
        print(f"  ✓ Project: {result.project_path}")
        print()
        print("Next steps:")
        print("  anolis-workbench")
        print("  → open http://127.0.0.1:3010 → select project → Launch")
    print("─" * 60)
    return 0


def main() -> int:
    args = _parse_args()

    if args.command is None:
        # No subcommand given — print help
        print("Usage: anolis-provision <command> [options]", file=sys.stderr)
        print("", file=sys.stderr)
        print("Commands:", file=sys.stderr)
        print("  install   Install binaries and create a project locally", file=sys.stderr)
        print("  bundle    (pass 4 — not yet implemented)", file=sys.stderr)
        print("  remote    (pass 5 — not yet implemented)", file=sys.stderr)
        return 2

    if args.command == "install":
        return _run_install(args)

    print(f"Command '{args.command}' is not yet implemented.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
