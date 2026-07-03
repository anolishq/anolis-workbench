"""Workspace project authoring for Anolis provisioning.

Creates ready-to-launch workbench projects from bundled templates or a
custom system.json. Deployment (binaries, /opt/anolis, systemd) is
delegated to the canonical anolis install.sh — see core/deploy.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from anolis_workbench.core import paths as paths_module
from anolis_workbench.core.executor import Executor, LocalExecutor

# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

_RUNTIME_BINARY_NAME = "anolis-runtime"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InstallerError(RuntimeError):
    """Base error for provisioning failures."""


# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict[str, bool]] = {
    "manual": {"observability": False, "telemetry_export": False},
    "telemetry": {"observability": True, "telemetry_export": True},
    "automation": {"observability": False, "telemetry_export": False},
    "full": {"observability": True, "telemetry_export": True},
}

VALID_PROFILES = list(PROFILES.keys())


def profile_includes(profile: str, feature: str) -> bool:
    """Check if a profile includes a given feature."""
    return PROFILES.get(profile, {}).get(feature, False)


# ---------------------------------------------------------------------------
# Project provisioning
# ---------------------------------------------------------------------------


def provision_project(
    template_name: str,
    project_name: str,
    install_prefix: Path,
    *,
    force: bool = False,
    executor: Executor | None = None,
    systems_root: Path | None = None,
    system_path: Path | None = None,
) -> Path:
    """Create a workbench project from a bundled template or custom system.json.

    Loads the template (or custom system.json), patches executable paths to point
    at the install prefix, validates, renders all configs, and writes them to disk.

    Args:
        template_name: Template directory name under templates/ (e.g. "bioreactor-manual").
        project_name: Name for the created project (e.g. "bioreactor-v1").
        install_prefix: Binary install prefix (e.g. /opt/anolis).
        force: If True, overwrite an existing project.
        executor: Executor for file writes. Defaults to LocalExecutor.
        systems_root: Override systems root (for remote targets). Defaults to local SYSTEMS_ROOT.
        system_path: Optional path to a custom system.json (overrides template_name).

    Returns:
        Path to the created project directory.

    Raises:
        FileNotFoundError: If the template/system file doesn't exist.
        ValueError: If the project exists and force=False.
    """
    from datetime import datetime, timezone

    if executor is None:
        executor = LocalExecutor()
    if systems_root is None:
        systems_root = paths_module.SYSTEMS_ROOT

    project_dir = systems_root / project_name
    if not force and executor.file_exists(str(project_dir)):
        raise ValueError(f"Project '{project_name}' already exists at {project_dir}. Use --force to overwrite.")

    # Load system definition from custom path or template
    if system_path is not None:
        if not system_path.exists():
            raise FileNotFoundError(f"System file not found: {system_path}")
        system: dict = json.loads(system_path.read_text(encoding="utf-8"))
    else:
        tpl_path = paths_module.TEMPLATES_ROOT / template_name / "system.json"
        if not tpl_path.exists():
            raise FileNotFoundError(f"Template '{template_name}' not found at {tpl_path}")
        system = json.loads(tpl_path.read_text(encoding="utf-8"))

    # Patch meta
    system["meta"]["name"] = project_name
    system["meta"]["created"] = datetime.now(timezone.utc).isoformat()

    # Patch paths — point executables at the install prefix bin directory
    bin_dir = install_prefix / "bin"
    system["paths"]["runtime_executable"] = str(bin_dir / _RUNTIME_BINARY_NAME)

    for _provider_id, provider_data in system["paths"].get("providers", {}).items():
        original_exe = Path(provider_data.get("executable", ""))
        binary_name = original_exe.name
        provider_data["executable"] = str(bin_dir / binary_name)

    # Write project files via executor
    write_project_files(executor, system, project_name, systems_root)

    return project_dir


def write_project_files(
    executor: Executor,
    system: dict,
    project_name: str,
    systems_root: Path,
) -> None:
    """Validate, render, and write project configs via the given executor."""
    from anolis_workbench.core import renderer as renderer_module
    from anolis_workbench.core.projects import validate_system_payload

    # Validate locally before writing
    errors = validate_system_payload(system)
    if errors:
        raise ValueError(f"System validation failed: {errors}")

    project_dir_str = str(systems_root / project_name)
    executor.mkdir(project_dir_str)

    # Write system.json
    system_json = json.dumps(system, indent=2).encode("utf-8")
    executor.write_file(f"{project_dir_str}/system.json", system_json)

    # Render and write config files
    rendered = renderer_module.render(system, project_name, systems_dir_name=systems_root.name)
    for rel_path, content in rendered.items():
        full_path = f"{project_dir_str}/{rel_path}"
        # Ensure parent dir exists for provider configs
        parent = str(Path(full_path).parent)
        if parent != project_dir_str:
            executor.mkdir(parent)
        executor.write_file(full_path, content.encode("utf-8"))
