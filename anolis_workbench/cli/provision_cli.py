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

    # --- bundle subcommand (pass 4) ---
    bundle_parser = subparsers.add_parser(
        "bundle",
        help="Download binaries and create an offline install bundle.",
    )
    bundle_parser.add_argument(
        "--project",
        default="bioreactor-v1",
        help="Project name for the bundle (default: bioreactor-v1).",
    )
    bundle_parser.add_argument(
        "--template",
        default="bioreactor-manual",
        help="Template to use for config rendering (default: bioreactor-manual).",
    )
    bundle_parser.add_argument(
        "--arch",
        required=True,
        choices=["arm64", "aarch64", "x86_64"],
        help="Target architecture (required — no auto-detect in bundle mode).",
    )
    bundle_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for the bundle.",
    )
    bundle_parser.add_argument(
        "--install-prefix",
        type=Path,
        default=Path("/usr/local"),
        help="Target install prefix for path patching (default: /usr/local).",
    )
    bundle_parser.add_argument(
        "--compat-matrix",
        type=Path,
        default=None,
        help="Override path to compatibility-matrix.yaml (for testing).",
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


def _run_bundle(args: argparse.Namespace) -> int:
    """Execute the bundle subcommand."""
    import os

    import requests

    from anolis_workbench.core import bundler

    # Map arch flag to platform string
    arch_map = {"arm64": "linux-arm64", "aarch64": "linux-arm64", "x86_64": "linux-x86_64"}
    platform_str = arch_map[args.arch]
    token = os.environ.get("GITHUB_TOKEN")

    print("Anolis Provision — Bundle")
    print(f"  Project:  {args.project}")
    print(f"  Template: {args.template}")
    print(f"  Arch:     {args.arch} → {platform_str}")
    print(f"  Output:   {args.out}")
    print(f"  Prefix:   {args.install_prefix}")
    print()

    # 1. Resolve components
    _print_progress("compat", "Loading compatibility matrix")
    matrix = installer.load_compat_matrix(args.compat_matrix)
    _print_progress("resolve", "Resolving components")
    components = installer.resolve_components(matrix)
    if not components:
        print("ERROR: No components found in compatibility matrix", file=sys.stderr)
        return 1

    # Filter to template-relevant providers
    template_providers = installer._get_template_provider_names(args.template)
    if template_providers is not None:
        components = [c for c in components if c.name == "anolis" or c.binary_name in template_providers]

    # 2. Fetch manifests + download tarballs
    session = requests.Session()
    tarballs: list[tuple[installer.ComponentSpec, bytes]] = []

    for comp in components:
        _print_progress("manifest", f"Fetching manifest for {comp.name} v{comp.version}")
        manifest = installer.fetch_manifest(session, comp.repo, comp.version, platform_str, token=token)
        _print_progress("download", f"Downloading {manifest.asset_name}")
        data = installer.download_and_verify(session, manifest.download_url, manifest.sha256, token=token)
        _print_progress("verified", f"SHA256 verified: {manifest.asset_name}")
        tarballs.append((comp, data))

    # 3. Build bundle
    _print_progress("project", "Building bundle")
    workbench_version = matrix.get("workbench_version", "")
    try:
        result = bundler.build_bundle(
            components=components,
            tarballs=tarballs,
            template_name=args.template,
            project_name=args.project,
            platform_str=platform_str,
            out_dir=args.out,
            install_prefix=args.install_prefix,
            workbench_version=workbench_version,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    # Print summary
    print()
    print("─" * 60)
    print("Bundle created!")
    print()
    print(f"  📁 {result.bundle_path}")
    print(f"  🎯 Platform: {result.platform}")
    print("  📦 Components:")
    for comp in result.components:
        print(f"       {comp.binary_name} v{comp.version}")
    print()
    print("Transfer to RPi and run:")
    print(f"  cd {result.bundle_path.name} && chmod +x install.sh && ./install.sh")
    print("─" * 60)
    return 0


def main() -> int:
    args = _parse_args()

    if args.command is None:
        print("Usage: anolis-provision <command> [options]", file=sys.stderr)
        print("", file=sys.stderr)
        print("Commands:", file=sys.stderr)
        print("  install   Install binaries and create a project locally", file=sys.stderr)
        print("  bundle    Download binaries and create an offline install bundle", file=sys.stderr)
        print("  remote    (pass 5 — not yet implemented)", file=sys.stderr)
        return 2

    if args.command == "install":
        return _run_install(args)

    if args.command == "bundle":
        return _run_bundle(args)

    print(f"Command '{args.command}' is not yet implemented.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
