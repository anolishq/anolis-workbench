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
        "--system",
        type=Path,
        default=None,
        help="Path to a custom system.json (mutually exclusive with --template).",
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
    install_parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip preflight checks before install.",
    )
    install_parser.add_argument(
        "--systemd",
        action="store_true",
        help="Install and enable a systemd service for the runtime.",
    )
    install_parser.add_argument(
        "--wait-ready",
        action="store_true",
        help="After start, poll the runtime health endpoint until ready.",
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
        "--system",
        type=Path,
        default=None,
        help="Path to a custom system.json (mutually exclusive with --template).",
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
    bundle_parser.add_argument(
        "--include-wheels",
        action="store_true",
        help="Bundle Python wheels for fully offline install (true air-gap).",
    )

    # --- remote subcommand (pass 5) ---
    remote_parser = subparsers.add_parser(
        "remote",
        help="Provision a remote machine via SSH (downloads locally, installs remotely).",
    )
    remote_parser.add_argument(
        "--target",
        required=True,
        help="SSH target in user@host format.",
    )
    remote_parser.add_argument(
        "--project",
        default="bioreactor-v1",
        help="Project name to create on the target (default: bioreactor-v1).",
    )
    remote_parser.add_argument(
        "--template",
        default="bioreactor-manual",
        help="Template to use for project creation (default: bioreactor-manual).",
    )
    remote_parser.add_argument(
        "--system",
        type=Path,
        default=None,
        help="Path to a custom system.json (mutually exclusive with --template).",
    )
    remote_parser.add_argument(
        "--install-prefix",
        type=Path,
        default=Path("/usr/local"),
        help="Binary install prefix on the target (default: /usr/local).",
    )
    remote_parser.add_argument(
        "--compat-matrix",
        type=Path,
        default=None,
        help="Override path to compatibility-matrix.yaml (for testing).",
    )
    remote_parser.add_argument(
        "--key",
        type=Path,
        default=None,
        help="Path to SSH private key file.",
    )
    remote_parser.add_argument(
        "--port",
        type=int,
        default=22,
        help="SSH port (default: 22).",
    )
    remote_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing binaries and project on target.",
    )
    remote_parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip preflight checks before install.",
    )
    remote_parser.add_argument(
        "--systemd",
        action="store_true",
        help="Install and enable a systemd service for the runtime.",
    )
    remote_parser.add_argument(
        "--wait-ready",
        action="store_true",
        help="After start, poll the runtime health endpoint until ready.",
    )

    return parser.parse_args()


def _print_progress(step: str, detail: str = "") -> None:
    """Print structured progress to stdout."""
    prefix_map = {
        "preflight": "🔍",
        "preflight_ok": "✓ ",
        "preflight_warn": "⚠️ ",
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
        "systemd": "⚙️ ",
        "health": "💓",
        "dry_run": "🔍",
        "done": "✅",
    }
    icon = prefix_map.get(step, "  ")
    msg = f"  {icon} {detail}" if detail else f"  {icon} {step}"
    print(msg)


def _validate_system_template(args: argparse.Namespace) -> bool:
    """Validate --system and --template mutual exclusivity.

    Returns True if valid, False if error was printed.
    """
    if hasattr(args, "system") and args.system is not None and args.template != "bioreactor-manual":
        print("ERROR: --system and --template are mutually exclusive", file=sys.stderr)
        return False
    return True


def _run_install(args: argparse.Namespace) -> int:
    """Execute the install subcommand."""
    import os

    if not _validate_system_template(args):
        return 1

    token = os.environ.get("GITHUB_TOKEN")

    print("Anolis Provision — Install")
    print(f"  Project:  {args.project}")
    if args.system:
        print(f"  System:   {args.system}")
    else:
        print(f"  Template: {args.template}")
    print(f"  Prefix:   {args.install_prefix}")
    if args.dry_run:
        print("  Mode:     DRY RUN")
    print()

    try:
        result = installer.install(
            project_name=args.project,
            template_name=args.template,
            system_path=args.system,
            install_prefix=args.install_prefix,
            compat_matrix_path=args.compat_matrix,
            github_token=token,
            force=args.force,
            dry_run=args.dry_run,
            skip_preflight=args.no_preflight,
            progress_callback=_print_progress,
        )
    except installer.InstallerError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    # Systemd service install (if requested and not dry-run)
    if args.systemd and not args.dry_run:
        from anolis_workbench.core import systemd

        _print_progress("systemd", f"Installing systemd service: {systemd.service_name(args.project)}")
        from anolis_workbench.core import paths as paths_module

        svc_result = systemd.install_service(
            args.project,
            install_prefix=args.install_prefix,
            systems_root=paths_module.SYSTEMS_ROOT,
            user=os.environ.get("USER", "root"),
        )
        if svc_result.error:
            print(f"\nWARNING: systemd: {svc_result.error}", file=sys.stderr)
        else:
            _print_progress("systemd", f"Service {svc_result.service_name} started")

        # Health check (if requested)
        if args.wait_ready:
            _print_progress("health", "Waiting for runtime to become ready...")
            ready = systemd.wait_ready()
            if ready:
                _print_progress("health", "Runtime is ready")
            else:
                print("\nWARNING: Runtime did not become ready within 30s", file=sys.stderr)

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

    if not _validate_system_template(args):
        return 1

    # Map arch flag to platform string
    arch_map = {"arm64": "linux-arm64", "aarch64": "linux-arm64", "x86_64": "linux-x86_64"}
    platform_str = arch_map[args.arch]
    token = os.environ.get("GITHUB_TOKEN")

    print("Anolis Provision — Bundle")
    print(f"  Project:  {args.project}")
    if args.system:
        print(f"  System:   {args.system}")
    else:
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

    # Filter to providers needed by the system or template
    if args.system:
        system_providers = installer.get_system_provider_names(args.system)
        if system_providers is not None:
            components = [c for c in components if c.name == "anolis" or c.binary_name in system_providers]
    else:
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
            system_path=args.system,
            include_wheels=args.include_wheels,
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


def _parse_target(target: str) -> tuple[str, str]:
    """Parse user@host target string. Returns (user, host)."""
    if "@" not in target:
        raise ValueError(f"Invalid target '{target}' — expected user@host format")
    user, host = target.split("@", 1)
    if not user or not host:
        raise ValueError(f"Invalid target '{target}' — expected user@host format")
    return user, host


def _run_remote(args: argparse.Namespace) -> int:
    """Execute the remote subcommand."""
    import os
    import uuid

    from anolis_workbench.core.executor import SubprocessSSHExecutor

    if not _validate_system_template(args):
        return 1

    try:
        user, host = _parse_target(args.target)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    token = os.environ.get("GITHUB_TOKEN")
    session_id = uuid.uuid4().hex[:12]

    print("Anolis Provision — Remote")
    print(f"  Target:   {user}@{host}")
    print(f"  Project:  {args.project}")
    print(f"  Template: {args.template}")
    print(f"  Prefix:   {args.install_prefix}")
    print(f"  Session:  {session_id}")
    print()

    # Create SSH executor
    executor = SubprocessSSHExecutor(
        host=host,
        user=user,
        key_file=str(args.key) if args.key else None,
        port=args.port,
    )

    # Verify SSH connectivity
    _print_progress("platform", f"Connecting to {user}@{host}")
    arch_result = executor.run(["uname", "-m"])
    if arch_result.returncode != 0:
        print(f"\nERROR: SSH connection failed: {arch_result.stderr.strip()}", file=sys.stderr)
        return 1

    remote_arch = arch_result.stdout.strip()
    _print_progress("platform", f"Remote architecture: {remote_arch}")

    if remote_arch not in ("aarch64", "arm64", "x86_64"):
        print(f"\nERROR: Unsupported remote architecture: {remote_arch}", file=sys.stderr)
        return 1

    # Determine remote systems root
    home_result = executor.run(["sh", "-c", "echo $HOME"])
    remote_home = home_result.stdout.strip() or f"/home/{user}"
    systems_root = Path(f"{remote_home}/.anolis/systems")

    # Run the full install flow using the SSH executor
    try:
        result = installer.install(
            project_name=args.project,
            template_name=args.template,
            system_path=args.system,
            install_prefix=args.install_prefix,
            compat_matrix_path=args.compat_matrix,
            github_token=token,
            force=args.force,
            skip_preflight=args.no_preflight,
            progress_callback=_print_progress,
            executor=executor,
            systems_root=systems_root,
        )
    except installer.InstallerError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    # Systemd service install (if requested)
    if args.systemd:
        from anolis_workbench.core import systemd

        _print_progress("systemd", f"Installing systemd service: {systemd.service_name(args.project)}")
        svc_result = systemd.install_service(
            args.project,
            executor=executor,
            install_prefix=args.install_prefix,
            systems_root=systems_root,
            user=user,
        )
        if svc_result.error:
            print(f"\nWARNING: systemd: {svc_result.error}", file=sys.stderr)
        else:
            _print_progress("systemd", f"Service {svc_result.service_name} started")

        # Health check (if requested)
        if args.wait_ready:
            _print_progress("health", "Waiting for runtime to become ready...")
            ready = systemd.wait_ready(executor)
            if ready:
                _print_progress("health", "Runtime is ready")
            else:
                print("\nWARNING: Runtime did not become ready within 30s", file=sys.stderr)

    # Print summary
    print()
    print("─" * 60)
    print(f"Remote provisioning complete! ({user}@{host})")
    print()
    for binary, version in result.verified_versions.items():
        print(f"  ✓ {binary:<30} {version}")
    print()
    print(f"  ✓ Project: {result.project_path}")
    print()
    print("Next steps (on the RPi):")
    print("  pip install anolis-workbench")
    print("  anolis-workbench")
    print("  → open http://<rpi-ip>:3010 → select project → Launch")
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
        print("  remote    Provision a remote machine via SSH", file=sys.stderr)
        return 2

    if args.command == "install":
        return _run_install(args)

    if args.command == "bundle":
        return _run_bundle(args)

    if args.command == "remote":
        return _run_remote(args)

    print(f"Command '{args.command}' is not yet implemented.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
