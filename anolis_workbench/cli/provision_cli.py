"""CLI entry point for `anolis-provision` — local provisioning of Anolis components."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from anolis_workbench.core import deploy, installer, releases
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
        "--force",
        action="store_true",
        help="Overwrite an existing workspace project.",
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run install.sh in dry-run mode (the workspace project is still created).",
    )
    install_parser.add_argument(
        "--no-start",
        action="store_true",
        help="Install but do not start the runtime service.",
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
        help="Overwrite an existing local workspace project.",
    )
    remote_parser.add_argument(
        "--no-start",
        action="store_true",
        help="Install but do not start the runtime service on the target.",
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
        help="Run install.sh in dry-run mode on each target.",
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
        "--install-prefix",
        type=Path,
        default=DEFAULT_INSTALL_PREFIX,
        help="Binary install prefix (default: /opt/anolis).",
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
    runtime_version: str,
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

    # The observability stack ships with every runtime release — use the
    # version that was just deployed.
    rt_version = runtime_version
    rt_repo = releases.RUNTIME_REPO

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


def _provision_workspace(args: argparse.Namespace) -> Path:
    """Authoring: ensure the local workspace project exists (source of truth)."""
    from anolis_workbench.core import paths as paths_module

    project_dir: Path = paths_module.SYSTEMS_ROOT / args.project
    if project_dir.exists() and not args.force and args.system is None:
        _print_progress("project", f"Using existing workspace project: {project_dir}")
        return project_dir
    return installer.provision_project(
        args.template,
        args.project,
        args.install_prefix,
        force=args.force,
        system_path=args.system,
    )


def _run_install(args: argparse.Namespace) -> int:
    """Execute the install subcommand: author the workspace, deploy via install.sh."""
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

    # 1. Authoring — the workspace project (unchanged concern).
    try:
        project_dir = _provision_workspace(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    system = json.loads((project_dir / "system.json").read_text(encoding="utf-8"))

    # 2. Deploy — delegated to the canonical anolis install.sh.
    try:
        result = deploy.deploy_local(
            system=system,
            project_name=args.project,
            workspace_dir=project_dir,
            prefix=args.install_prefix,
            no_start=args.no_start,
            dry_run=args.dry_run,
            with_telemetry_export=_wants_telemetry_export(args),
            progress_callback=_print_progress,
        )
    except deploy.DeployError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    # Observability stack (if requested and not dry-run). Still workbench-owned
    # until install.sh gains --with-observability (anolishq/anolis#162).
    if _wants_observability(args) and not args.dry_run:
        _run_observability_step(args, result.runtime_version, token)

    # Workbench systemd service (appliance mode, if requested and not dry-run)
    if getattr(args, "workbench_service", False) and not args.dry_run:
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
    if args.dry_run:
        print("DRY RUN complete — no changes were made to the target.")
    else:
        print("Installation complete!")
        print()
        print(f"  ✓ Runtime: anolis-runtime v{result.runtime_version} ({result.prefix})")
        print(f"  ✓ Project: {project_dir}")
        print()
        print("Next steps:")
        print("  systemctl status anolis-runtime")
        print("  curl http://127.0.0.1:8080/v0/status   # runtime API")
    print("─" * 60)
    return 0


def _load_system_for_deploy(template: str, system_path: Path | None) -> tuple[dict, Path]:
    """Load a system definition + the dir its behavior files resolve against."""
    from anolis_workbench.core import paths as paths_module

    if system_path is not None:
        if not system_path.exists():
            raise FileNotFoundError(f"System file not found: {system_path}")
        return json.loads(system_path.read_text(encoding="utf-8")), system_path.parent
    tpl_dir = paths_module.TEMPLATES_ROOT / template
    tpl_path = tpl_dir / "system.json"
    if not tpl_path.exists():
        raise FileNotFoundError(f"Template '{template}' not found at {tpl_path}")
    return json.loads(tpl_path.read_text(encoding="utf-8")), tpl_dir


def _run_bundle(args: argparse.Namespace) -> int:
    """Execute the bundle subcommand: build a canonical offline bundle via install.sh --stage."""
    if not _validate_system_template(args):
        return 1

    arch = "arm64" if args.arch in ("arm64", "aarch64") else "x86_64"

    print("Anolis Provision — Bundle")
    print(f"  Project:  {args.project}")
    if args.system:
        print(f"  System:   {args.system}")
    else:
        print(f"  Template: {args.template}")
    print(f"  Arch:     {arch}")
    print(f"  Output:   {args.out}")
    print(f"  Prefix:   {args.install_prefix}")
    print()

    try:
        system, workspace_dir = _load_system_for_deploy(args.template, args.system)
        tarball = deploy.stage_bundle(
            system=system,
            project_name=args.project,
            workspace_dir=workspace_dir,
            out_dir=args.out,
            arch=arch,
            prefix=args.install_prefix,
            progress_callback=_print_progress,
        )
    except (FileNotFoundError, deploy.DeployError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    # Print summary
    print()
    print("─" * 60)
    print("Bundle created!")
    print()
    print(f"  📁 {tarball}")
    print()
    print("Transfer to the device and run:")
    print(f"  sudo ./install.sh --local {tarball.name}")
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

    # 1. Authoring — the LOCAL workspace project is the source of truth.
    try:
        project_dir = _provision_workspace(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    system = json.loads((project_dir / "system.json").read_text(encoding="utf-8"))

    # 2. Deploy — push the config to the target and run install.sh there.
    try:
        result = deploy.deploy_remote(
            executor=executor,
            system=system,
            project_name=args.project,
            workspace_dir=project_dir,
            prefix=args.install_prefix,
            no_start=args.no_start,
            with_telemetry_export=_wants_telemetry_export(args),
            progress_callback=_print_progress,
        )
    except deploy.DeployError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    # Observability stack (if requested) — runs on THIS machine (docker stack).
    # Still workbench-owned until install.sh gains --with-observability (#162).
    if _wants_observability(args):
        _run_observability_step(args, result.runtime_version, token)

    # Print summary
    print()
    print("─" * 60)
    print(f"Remote provisioning complete! ({user}@{host})")
    print()
    print(f"  ✓ Runtime: anolis-runtime v{result.runtime_version} ({result.prefix})")
    print(f"  ✓ Project: {project_dir}")
    print()
    print("Next steps:")
    print(f"  ssh {user}@{host} systemctl status anolis-runtime")
    print(f"  curl http://{host}:8080/v0/status   # runtime API")
    print("─" * 60)
    return 0


def _provision_single_target(
    target: "fleet_module.FleetTarget",
    *,
    dry_run: bool = False,
) -> "fleet_module.TargetResult":
    """Provision a single fleet target. Used as the callback for fleet execution."""
    from anolis_workbench.core import fleet as fleet_module
    from anolis_workbench.core.executor import SubprocessSSHExecutor

    # Parse host
    if "@" not in target.host:
        return fleet_module.TargetResult(
            name=target.name, host=target.host, success=False, error="Invalid host format (expected user@host)"
        )

    user, host = target.host.split("@", 1)

    executor = SubprocessSSHExecutor(
        host=host,
        user=user,
        key_file=target.key,
    )

    try:
        system, workspace_dir = _load_system_for_deploy(target.template, None)
        result = deploy.deploy_remote(
            executor=executor,
            system=system,
            project_name=target.project,
            workspace_dir=workspace_dir,
            prefix=target.install_prefix,
            dry_run=dry_run,
        )
        components = [f"anolis-runtime {result.runtime_version}"]
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
        return _provision_single_target(target, dry_run=dry_run)

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

    target_label = args.target or "localhost"
    print("Anolis Provision — Rollback")
    print(f"  Target:   {target_label}")
    print(f"  Prefix:   {args.install_prefix}")
    print()

    result = rollback_module.rollback(
        args.install_prefix,
        executor=executor,
    )

    if not result.success:
        print(f"ERROR: {result.error}", file=sys.stderr)
        return 1

    print("─" * 60)
    print("Rollback complete (via install.sh --rollback):")
    for line in result.output.strip().splitlines()[-6:]:
        print(f"  {line}")
    print("─" * 60)
    return 0


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
