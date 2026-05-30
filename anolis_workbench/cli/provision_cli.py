"""CLI entry point for `anolis-provision` — local provisioning of Anolis components."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from anolis_workbench.core import installer
from anolis_workbench.core.installer import VALID_PROFILES, profile_includes
from anolis_workbench.core.paths import DEFAULT_INSTALL_PREFIX

if TYPE_CHECKING:
    from anolis_workbench.core import fleet as fleet_module


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
        default=DEFAULT_INSTALL_PREFIX,
        help="Binary install prefix (default: /opt/anolis).",
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
    install_parser.add_argument(
        "--profile",
        choices=VALID_PROFILES,
        default="manual",
        help="Provisioning profile: manual, telemetry, automation, full (default: manual).",
    )
    install_parser.add_argument(
        "--with-observability",
        action="store_true",
        help="Deploy the Docker-based observability stack (InfluxDB + Grafana).",
    )
    install_parser.add_argument(
        "--with-telemetry-export",
        action="store_true",
        help="Install and configure the telemetry export service.",
    )
    install_parser.add_argument(
        "--start-observability",
        action="store_true",
        help="Auto-start the observability stack with docker compose (requires Docker).",
    )
    install_parser.add_argument(
        "--behavior-tree",
        type=Path,
        default=None,
        help="Path to a behavior tree XML file (required for automation/full profiles).",
    )
    install_parser.add_argument(
        "--workbench-service",
        action="store_true",
        help="Install a systemd service to auto-start the workbench UI on boot (appliance mode).",
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
        default=DEFAULT_INSTALL_PREFIX,
        help="Target install prefix for path patching (default: /opt/anolis).",
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
        default=DEFAULT_INSTALL_PREFIX,
        help="Binary install prefix on the target (default: /opt/anolis).",
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
    remote_parser.add_argument(
        "--profile",
        choices=VALID_PROFILES,
        default="manual",
        help="Provisioning profile: manual, telemetry, automation, full (default: manual).",
    )
    remote_parser.add_argument(
        "--with-observability",
        action="store_true",
        help="Deploy the Docker-based observability stack (InfluxDB + Grafana).",
    )
    remote_parser.add_argument(
        "--with-telemetry-export",
        action="store_true",
        help="Install and configure the telemetry export service.",
    )
    remote_parser.add_argument(
        "--start-observability",
        action="store_true",
        help="Auto-start the observability stack with docker compose (requires Docker).",
    )
    remote_parser.add_argument(
        "--behavior-tree",
        type=Path,
        default=None,
        help="Path to a behavior tree XML file (required for automation/full profiles).",
    )

    # --- fleet subcommand (pass 9) ---
    fleet_parser = subparsers.add_parser(
        "fleet",
        help="Provision multiple targets from a fleet.yaml file.",
    )
    fleet_parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Path to fleet.yaml defining targets.",
    )
    fleet_parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated list of target names to provision.",
    )
    fleet_parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Max parallel jobs (default: 4). Use 1 for serial mode.",
    )
    fleet_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and verify but do not install.",
    )
    fleet_parser.add_argument(
        "--compat-matrix",
        type=Path,
        default=None,
        help="Override path to compatibility-matrix.yaml (for testing).",
    )

    # --- rollback subcommand (pass 9) ---
    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Roll back binaries to previous version.",
    )
    rollback_parser.add_argument(
        "--target",
        default=None,
        help="SSH target in user@host format (omit for local rollback).",
    )
    rollback_parser.add_argument(
        "--project",
        default="bioreactor-v1",
        help="Project name (default: bioreactor-v1).",
    )
    rollback_parser.add_argument(
        "--install-prefix",
        type=Path,
        default=DEFAULT_INSTALL_PREFIX,
        help="Binary install prefix (default: /opt/anolis).",
    )
    rollback_parser.add_argument(
        "--systemd",
        action="store_true",
        help="Restart the systemd service after rollback.",
    )
    rollback_parser.add_argument(
        "--compat-matrix",
        type=Path,
        default=None,
        help="Override path to compatibility-matrix.yaml (for testing).",
    )
    rollback_parser.add_argument(
        "--key",
        type=Path,
        default=None,
        help="Path to SSH private key file (for remote rollback).",
    )
    rollback_parser.add_argument(
        "--port",
        type=int,
        default=22,
        help="SSH port (default: 22).",
    )
    rollback_parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Fleet file for fleet-wide rollback.",
    )

    # --- check-update subcommand ---
    subparsers.add_parser(
        "check-update",
        help="Check if a newer workbench version is available on GitHub.",
    )

    # --- update subcommand ---
    update_parser = subparsers.add_parser(
        "update",
        help="Download and install the latest version.",
    )
    update_parser.add_argument(
        "--install-prefix",
        type=Path,
        default=DEFAULT_INSTALL_PREFIX,
        help="Binary install prefix (default: /opt/anolis).",
    )
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without executing.",
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


def _wants_observability(args: argparse.Namespace) -> bool:
    """Check if observability should be deployed (profile or explicit flag)."""
    return args.with_observability or profile_includes(args.profile, "observability")


def _wants_telemetry_export(args: argparse.Namespace) -> bool:
    """Check if telemetry export should be installed (profile or explicit flag)."""
    return args.with_telemetry_export or profile_includes(args.profile, "telemetry_export")


def _run_observability_step(
    args: argparse.Namespace,
    result: installer.InstallResult,
    token: str | None,
) -> None:
    """Download and deploy the observability stack."""
    import requests

    from anolis_workbench.core import observability

    # Check Docker if --start-observability
    if args.start_observability:
        available, detail = observability.check_docker_available()
        if not available:
            print(f"\nWARNING: {detail} — stack will be extracted but not started", file=sys.stderr)
            args.start_observability = False

    # Get runtime version from compat matrix
    matrix = installer.load_compat_matrix(getattr(args, "compat_matrix", None))
    rt_version = matrix.get("runtime", {}).get("version", "")
    rt_repo = matrix.get("runtime", {}).get("repo", "anolishq/anolis")

    # Download the observability tarball from the runtime release
    asset_name = f"anolis-{rt_version}-observability.tar.gz"
    _print_progress("download", f"Downloading observability stack: {asset_name}")

    session = requests.Session()
    headers: dict[str, str] = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://github.com/{rt_repo}/releases/download/v{rt_version}/{asset_name}"
    resp = session.get(url, headers=headers, timeout=120)
    if resp.status_code != 200:
        print(f"\nWARNING: Failed to download observability stack ({resp.status_code})", file=sys.stderr)
        return

    _print_progress("install", "Deploying observability stack")
    obs_result = observability.deploy_observability(
        resp.content,
        start=args.start_observability,
    )

    if obs_result.error:
        print(f"\nWARNING: observability: {obs_result.error}", file=sys.stderr)
    elif obs_result.started:
        _print_progress("done", f"Observability stack running at {obs_result.stack_path}")
    else:
        _print_progress("done", f"Observability stack extracted to {obs_result.stack_path}")
        print(f"    To start: cd {obs_result.stack_path} && docker compose up -d")


def _run_telemetry_export_step(
    args: argparse.Namespace,
    result: installer.InstallResult,
) -> None:
    """Install and configure the telemetry export service."""
    import os

    from anolis_workbench.core import telemetry_config

    # Get version from compat matrix
    matrix = installer.load_compat_matrix(getattr(args, "compat_matrix", None))
    tel_version = matrix.get("optional_components", {}).get("telemetry_export", {}).get("version")
    if not tel_version:
        print("\nWARNING: telemetry_export version not found in compat matrix", file=sys.stderr)
        return

    # Install the package
    _print_progress("install", f"Installing anolis-telemetry-export v{tel_version}")
    success = telemetry_config.install_telemetry_export_package(tel_version)
    if not success:
        print("\nWARNING: Failed to install anolis-telemetry-export", file=sys.stderr)
        return

    # Render config
    _print_progress("project", "Rendering telemetry export config")
    config_path = telemetry_config.render_telemetry_config(
        args.project,
        systems_root=result.project_path.parent,
    )
    _print_progress("done", f"Config: {config_path}")
    print("    NOTE: Set INFLUXDB_TOKEN env var before starting the service.")

    # Install systemd unit if --systemd was also passed
    if getattr(args, "systemd", False):
        _print_progress("systemd", "Installing telemetry export systemd service")
        svc_result = telemetry_config.install_telemetry_service(
            args.project,
            config_path,
            user=os.environ.get("USER", "root"),
        )
        if svc_result.error:
            print(f"\nWARNING: telemetry systemd: {svc_result.error}", file=sys.stderr)
        else:
            _print_progress("systemd", f"Service {svc_result.service_name} installed")


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
    if args.profile != "manual":
        print(f"  Profile:  {args.profile}")
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

    # Observability stack (if requested and not dry-run)
    if _wants_observability(args) and not result.dry_run:
        _run_observability_step(args, result, token)

    # Telemetry export (if requested and not dry-run)
    if _wants_telemetry_export(args) and not result.dry_run:
        _run_telemetry_export_step(args, result)

    # Workbench systemd service (appliance mode, if requested and not dry-run)
    if getattr(args, "workbench_service", False) and not result.dry_run:
        from anolis_workbench.core import workbench_service

        _print_progress("systemd", "Installing workbench systemd service")
        wb_result = workbench_service.install_service(
            user=os.environ.get("USER", "root"),
        )
        if wb_result.error:
            print(f"\nWARNING: workbench service: {wb_result.error}", file=sys.stderr)
        else:
            _print_progress("systemd", "Workbench service enabled and started")

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

    # Observability stack (if requested)
    if _wants_observability(args):
        _run_observability_step(args, result, token)

    # Telemetry export (if requested)
    if _wants_telemetry_export(args):
        _run_telemetry_export_step(args, result)

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


def _provision_single_target(
    target: "fleet_module.FleetTarget",
    *,
    dry_run: bool = False,
    compat_matrix_path: Path | None = None,
) -> "fleet_module.TargetResult":
    """Provision a single fleet target. Used as the callback for fleet execution."""
    import os

    from anolis_workbench.core import fleet as fleet_module
    from anolis_workbench.core.executor import SubprocessSSHExecutor

    # Parse host
    if "@" not in target.host:
        return fleet_module.TargetResult(
            name=target.name, host=target.host, success=False, error="Invalid host format (expected user@host)"
        )

    user, host = target.host.split("@", 1)
    token = os.environ.get("GITHUB_TOKEN")

    executor = SubprocessSSHExecutor(
        host=host,
        user=user,
        key_file=target.key,
    )

    try:
        result = installer.install(
            project_name=target.project,
            template_name=target.template,
            install_prefix=target.install_prefix,
            compat_matrix_path=compat_matrix_path,
            github_token=token,
            force=False,
            dry_run=dry_run,
            skip_preflight=False,
            executor=executor,
        )
        components = [f"{b} {v}" for b, v in result.verified_versions.items()]
        return fleet_module.TargetResult(name=target.name, host=target.host, success=True, components=components)
    except Exception as exc:
        return fleet_module.TargetResult(name=target.name, host=target.host, success=False, error=str(exc))


def _run_fleet(args: argparse.Namespace) -> int:
    """Execute the fleet subcommand."""
    from anolis_workbench.core import fleet as fleet_module

    try:
        config = fleet_module.load_fleet_file(args.file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Filter targets
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    try:
        targets = fleet_module.filter_targets(config, only)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("Anolis Provision — Fleet")
    print(f"  Fleet file: {args.file}")
    print(f"  Targets:    {len(targets)}")
    print(f"  Jobs:       {args.jobs}")
    if args.dry_run:
        print("  Mode:       DRY RUN")
    print()

    def _provision_fn(target: fleet_module.FleetTarget, *, dry_run: bool = False) -> fleet_module.TargetResult:
        return _provision_single_target(target, dry_run=dry_run, compat_matrix_path=args.compat_matrix)

    result = fleet_module.provision_fleet(
        targets,
        jobs=args.jobs,
        dry_run=args.dry_run,
        provision_fn=_provision_fn,
    )

    print()
    print("─" * 60)
    print(fleet_module.format_fleet_result(result))
    print("─" * 60)
    return 0 if result.failed == 0 else 1


def _run_rollback(args: argparse.Namespace) -> int:
    """Execute the rollback subcommand."""
    from anolis_workbench.core import rollback as rollback_module
    from anolis_workbench.core.executor import LocalExecutor, SubprocessSSHExecutor

    # Determine executor
    executor: installer.Executor
    if args.target:
        try:
            user, host = _parse_target(args.target)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        executor = SubprocessSSHExecutor(
            host=host,
            user=user,
            key_file=str(args.key) if args.key else None,
            port=args.port,
        )
    else:
        executor = LocalExecutor()

    # Resolve binary names from compat matrix
    matrix = installer.load_compat_matrix(args.compat_matrix)
    components = installer.resolve_components(matrix)
    binary_names = [c.binary_name for c in components]

    target_label = args.target or "localhost"
    print("Anolis Provision — Rollback")
    print(f"  Target:   {target_label}")
    print(f"  Project:  {args.project}")
    print(f"  Prefix:   {args.install_prefix}")
    print(f"  Binaries: {', '.join(binary_names)}")
    print()

    result = rollback_module.rollback(
        binary_names,
        args.install_prefix,
        project_name=args.project,
        systemd=args.systemd,
        executor=executor,
    )

    if result.error and not result.rolled_back:
        print(f"ERROR: {result.error}", file=sys.stderr)
        return 1

    print("─" * 60)
    if result.rolled_back:
        print("Rollback complete:")
        for name in result.rolled_back:
            print(f"  ✓ {name} restored from .prev")
    if result.failed:
        print("Failed (no .prev backup):")
        for name in result.failed:
            print(f"  ✗ {name}")
    if result.service_restarted:
        print(f"  ✓ Service anolis-{args.project} restarted")
    if result.error:
        print(f"\n  ⚠️  {result.error}")
    print("─" * 60)
    return 0 if not result.failed else 1


def _run_check_update() -> int:
    """Check for available updates."""
    from anolis_workbench.core.updater import check_for_update

    status = check_for_update()
    print(f"  Current version: {status.current_version}")

    if status.error:
        print(f"  ⚠️  {status.error}")
        return 0

    print(f"  Latest version:  {status.latest_version}")
    if status.update_available:
        print(f"\n  ⬆️  Update available: {status.current_version} → {status.latest_version}")
        print("     Run install.sh or re-run anolis-provision install to update.")
    else:
        print("\n  ✓ You are on the latest version.")
    return 0


def _run_update(args: argparse.Namespace) -> int:
    """Download and install the latest version."""
    from anolis_workbench.core.updater import check_for_update, perform_update

    print("Anolis Provision — Update")
    print()

    status = check_for_update()
    print(f"  Current version: {status.current_version}")

    if status.error:
        print(f"  ⚠️  {status.error}")
        return 1

    if not status.update_available:
        print(f"  Latest version:  {status.latest_version}")
        print("\n  ✓ Already up to date.")
        return 0

    print(f"  Latest version:  {status.latest_version}")
    print(f"\n  ⬆️  Updating {status.current_version} → {status.latest_version}...")
    if args.dry_run:
        print("  Mode: DRY RUN")

    assert status.latest_version is not None  # guaranteed when update_available is True
    result = perform_update(
        target_version=status.latest_version,
        install_prefix=args.install_prefix,
        dry_run=args.dry_run,
    )

    if result.success:
        if args.dry_run:
            print(f"\n  {result.error}")
        else:
            print(f"\n  ✅ Updated to v{result.version}")
        return 0
    else:
        print(f"\n  ❌ Update failed: {result.error}", file=sys.stderr)
        return 1


def main() -> int:
    args = _parse_args()

    if args.command is None:
        print("Usage: anolis-provision <command> [options]", file=sys.stderr)
        print("", file=sys.stderr)
        print("Commands:", file=sys.stderr)
        print("  install       Install binaries and create a project locally", file=sys.stderr)
        print("  bundle        Download binaries and create an offline install bundle", file=sys.stderr)
        print("  remote        Provision a remote machine via SSH", file=sys.stderr)
        print("  fleet         Provision multiple targets from a fleet.yaml", file=sys.stderr)
        print("  rollback      Roll back binaries to previous version", file=sys.stderr)
        print("  check-update  Check if a newer version is available", file=sys.stderr)
        print("  update        Download and install the latest version", file=sys.stderr)
        return 2

    if args.command == "install":
        return _run_install(args)

    if args.command == "bundle":
        return _run_bundle(args)

    if args.command == "remote":
        return _run_remote(args)

    if args.command == "fleet":
        return _run_fleet(args)

    if args.command == "rollback":
        return _run_rollback(args)

    if args.command == "check-update":
        return _run_check_update()

    if args.command == "update":
        return _run_update(args)

    print(f"Command '{args.command}' is not yet implemented.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
