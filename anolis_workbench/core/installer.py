"""Local provisioning installer for Anolis components.

Downloads pre-built binaries from GitHub Releases, verifies integrity,
installs to a prefix (default /usr/local), and creates a ready-to-launch
workbench project from a bundled template.

No HTTP server dependency. Subprocess use limited to the install (tar) and
verification (--version) steps.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import requests
import yaml

from anolis_workbench.core import paths as paths_module
from anolis_workbench.core.executor import Executor, LocalExecutor
from anolis_workbench.core.preflight import run_preflight

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ComponentSpec:
    """A component to be installed (runtime or provider)."""

    name: str  # e.g. "anolis-provider-bread"
    repo: str  # e.g. "anolishq/anolis-provider-bread"
    version: str  # e.g. "0.2.8"
    binary_name: str  # e.g. "anolis-provider-bread"


@dataclass
class ManifestData:
    """Parsed release manifest for a single component + platform."""

    component: str
    version: str
    platform: str
    asset_name: str
    sha256: str
    download_url: str


@dataclass
class InstallResult:
    """Result summary from a full install run."""

    components: list[ComponentSpec]
    install_prefix: Path
    project_path: Path
    verified_versions: dict[str, str]
    dry_run: bool


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The runtime compat matrix key is "anolis" but the binary is "anolis-runtime".
# Provider keys match their binary names.
_RUNTIME_BINARY_NAME = "anolis-runtime"

_ARCH_MAP: dict[str, str] = {
    "aarch64": "linux-arm64",
    "arm64": "linux-arm64",
    "x86_64": "linux-x86_64",
}

_GITHUB_API_BASE = "https://api.github.com"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InstallerError(RuntimeError):
    """Base error for installer failures."""


class PlatformError(InstallerError):
    """Unsupported platform."""


class ManifestError(InstallerError):
    """Failed to fetch or parse a release manifest."""


class IntegrityError(InstallerError):
    """SHA256 verification failed."""


class InstallError(InstallerError):
    """Failed to install a tarball."""


class VerificationError(InstallerError):
    """Post-install version verification failed."""


class PreflightError(InstallerError):
    """Preflight checks failed with fatal errors."""


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
# Platform detection
# ---------------------------------------------------------------------------


def detect_platform() -> str:
    """Map the current machine architecture to a manifest platform string.

    Returns:
        Platform string like "linux-arm64" or "linux-x86_64".

    Raises:
        PlatformError: If the architecture is not supported.
    """
    machine = platform.machine()
    result = _ARCH_MAP.get(machine)
    if result is None:
        raise PlatformError(f"Unsupported architecture: {machine!r}. Supported: {', '.join(sorted(_ARCH_MAP.keys()))}")
    return result


# ---------------------------------------------------------------------------
# Compat matrix loading
# ---------------------------------------------------------------------------


def load_compat_matrix(matrix_path: Path | None = None) -> dict[str, Any]:
    """Load the compatibility matrix.

    Args:
        matrix_path: Optional override path (for testing or --compat-matrix).
                     If None, loads the bundled matrix from package data.
    """
    if matrix_path is not None:
        raw = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    bundled = resources.files("anolis_workbench").joinpath("schemas").joinpath("compatibility-matrix.yaml")
    return yaml.safe_load(bundled.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Component resolution
# ---------------------------------------------------------------------------


def resolve_components(compat_matrix: dict[str, Any]) -> list[ComponentSpec]:
    """Resolve the list of components to install from the compat matrix.

    Returns:
        List of ComponentSpec for the runtime and each provider that has
        a repo + version entry in the matrix.
    """
    components: list[ComponentSpec] = []

    # Runtime
    rt = compat_matrix.get("runtime", {})
    if rt.get("repo") and rt.get("version"):
        components.append(
            ComponentSpec(
                name="anolis",
                repo=rt["repo"],
                version=rt["version"],
                binary_name=_RUNTIME_BINARY_NAME,
            )
        )

    # Providers
    providers = compat_matrix.get("providers", {})
    for provider_name, pdata in providers.items():
        if not isinstance(pdata, dict):
            continue
        if pdata.get("repo") and pdata.get("version"):
            components.append(
                ComponentSpec(
                    name=provider_name,
                    repo=pdata["repo"],
                    version=pdata["version"],
                    binary_name=provider_name,
                )
            )

    return components


# ---------------------------------------------------------------------------
# GitHub Releases API
# ---------------------------------------------------------------------------


def _github_headers(token: str | None = None) -> dict[str, str]:
    """Build request headers for GitHub API calls."""
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_manifest(
    session: requests.Session,
    repo: str,
    version: str,
    platform_str: str,
    *,
    token: str | None = None,
) -> ManifestData:
    """Fetch the release manifest JSON for a component.

    The manifest asset is named `manifest-<platform>.json` and is attached
    to the GitHub release tagged `v<version>`.

    Raises:
        ManifestError: On network failure, missing release, or missing asset.
    """
    tag = f"v{version}"
    url = f"{_GITHUB_API_BASE}/repos/{repo}/releases/tags/{tag}"
    try:
        resp = session.get(url, headers=_github_headers(token), timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ManifestError(f"Failed to fetch release {tag} from {repo}: {exc}") from exc

    release = resp.json()
    manifest_name = f"manifest-{platform_str}.json"

    # Find the manifest asset
    manifest_asset = None
    for asset in release.get("assets", []):
        if asset.get("name") == manifest_name:
            manifest_asset = asset
            break

    if manifest_asset is None:
        raise ManifestError(
            f"Release {tag} of {repo} has no asset named {manifest_name!r}. "
            f"Available: {[a['name'] for a in release.get('assets', [])]}"
        )

    # Download and parse the manifest JSON
    manifest_url = manifest_asset["browser_download_url"]
    try:
        manifest_resp = session.get(manifest_url, headers=_github_headers(token), timeout=30)
        manifest_resp.raise_for_status()
    except requests.RequestException as exc:
        raise ManifestError(f"Failed to download manifest {manifest_name}: {exc}") from exc

    try:
        manifest_data = manifest_resp.json()
    except ValueError as exc:
        raise ManifestError(f"Invalid JSON in manifest {manifest_name}: {exc}") from exc

    # Find the tarball asset download URL
    tarball_asset_name = manifest_data.get("asset", "")
    tarball_url = None
    for asset in release.get("assets", []):
        if asset.get("name") == tarball_asset_name:
            tarball_url = asset["browser_download_url"]
            break

    if tarball_url is None:
        raise ManifestError(f"Tarball asset {tarball_asset_name!r} not found in release {tag} of {repo}")

    return ManifestData(
        component=manifest_data.get("component", ""),
        version=manifest_data.get("version", version),
        platform=manifest_data.get("platform", platform_str),
        asset_name=tarball_asset_name,
        sha256=manifest_data["sha256"],
        download_url=tarball_url,
    )


# ---------------------------------------------------------------------------
# Download + verify
# ---------------------------------------------------------------------------


def download_and_verify(
    session: requests.Session,
    url: str,
    expected_sha256: str,
    *,
    token: str | None = None,
) -> bytes:
    """Download a tarball and verify its SHA256 hash.

    Returns:
        The raw tarball bytes.

    Raises:
        ManifestError: On download failure.
        IntegrityError: If the SHA256 doesn't match.
    """
    try:
        resp = session.get(url, headers=_github_headers(token), timeout=120)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ManifestError(f"Failed to download {url}: {exc}") from exc

    data = resp.content
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise IntegrityError(f"SHA256 mismatch for {url}:\n  expected: {expected_sha256}\n  actual:   {actual_sha256}")
    return data


# ---------------------------------------------------------------------------
# Install tarball
# ---------------------------------------------------------------------------


def install_tarball(
    data: bytes,
    prefix: Path,
    *,
    executor: Executor | None = None,
    backup: bool = False,
    binary_names: list[str] | None = None,
) -> None:
    """Install a tarball to the given prefix using sudo tar.

    The tarball internal structure is `bin/<binary>`, so extracting to
    /usr/local yields /usr/local/bin/<binary>.

    Args:
        data: Raw tarball bytes.
        prefix: Install prefix (e.g. /usr/local).
        executor: Executor for I/O.
        backup: If True, back up existing binaries to .prev before overwriting.
        binary_names: Names of binaries to back up (required if backup=True).

    Raises:
        InstallError: If tar extraction fails.
    """
    if executor is None:
        executor = LocalExecutor()

    # Backup existing binaries before overwriting
    if backup and binary_names:
        from anolis_workbench.core.rollback import backup_binaries

        backup_binaries(binary_names, prefix, executor=executor)

    result = executor.run(["tar", "-xz", "-C", str(prefix)], input=data, sudo=True)
    if result.returncode != 0:
        raise InstallError(f"tar extraction failed (exit {result.returncode}): {result.stderr.strip()}")


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
        install_prefix: Binary install prefix (e.g. /usr/local).
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


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_installation(
    install_prefix: Path,
    components: list[ComponentSpec],
    *,
    executor: Executor | None = None,
) -> dict[str, str]:
    """Run --version on each installed binary and return version strings.

    Returns:
        Dict mapping binary_name → version string.

    Raises:
        VerificationError: If a binary cannot be executed or doesn't report
            the expected version.
    """
    if executor is None:
        executor = LocalExecutor()
    versions: dict[str, str] = {}
    bin_dir = install_prefix / "bin"

    for comp in components:
        binary_path = str(bin_dir / comp.binary_name)
        result = executor.run([binary_path, "--version"])

        if result.returncode != 0:
            if "not found" in result.stderr.lower() or "no such file" in result.stderr.lower():
                raise VerificationError(f"Binary not found: {binary_path}")
            raise VerificationError(f"{binary_path} --version exited with code {result.returncode}")

        version_output = result.stdout.strip()
        versions[comp.binary_name] = version_output

    return versions


# ---------------------------------------------------------------------------
# Existing binary check (Decision E)
# ---------------------------------------------------------------------------


def check_existing_binaries(
    install_prefix: Path,
    components: list[ComponentSpec],
    *,
    executor: Executor | None = None,
) -> dict[str, str | None]:
    """Check which binaries already exist and their versions.

    Returns:
        Dict mapping binary_name → version string (or None if not found).
    """
    if executor is None:
        executor = LocalExecutor()
    results: dict[str, str | None] = {}
    bin_dir = install_prefix / "bin"

    for comp in components:
        binary_path = str(bin_dir / comp.binary_name)
        if not executor.file_exists(binary_path):
            results[comp.binary_name] = None
            continue
        result = executor.run([binary_path, "--version"])
        if result.returncode == 0:
            results[comp.binary_name] = result.stdout.strip()
        else:
            results[comp.binary_name] = None

    return results


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def install(
    project_name: str,
    *,
    template_name: str = "bioreactor-manual",
    system_path: Path | None = None,
    install_prefix: Path = Path("/usr/local"),
    compat_matrix_path: Path | None = None,
    github_token: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    skip_preflight: bool = False,
    progress_callback: Any = None,
    executor: Executor | None = None,
    systems_root: Path | None = None,
) -> InstallResult:
    """Run the full install flow.

    Args:
        project_name: Project name to create (e.g. "bioreactor-v1").
        template_name: Template to use for project creation.
        system_path: Optional path to a custom system.json (overrides template).
        install_prefix: Where to install binaries (default /usr/local).
        compat_matrix_path: Override path to compat matrix YAML.
        github_token: Optional GitHub token for API auth.
        force: Overwrite existing binaries/project.
        dry_run: If True, fetch and verify but don't install.
        skip_preflight: If True, skip preflight checks.
        progress_callback: Optional callable(step: str, detail: str) for UI.
        executor: Executor for I/O operations. Defaults to LocalExecutor.
        systems_root: Override systems root (for remote targets).

    Returns:
        InstallResult with summary information.

    Raises:
        PreflightError: If preflight checks fail with fatal errors.
    """
    if executor is None:
        executor = LocalExecutor()
    if systems_root is None:
        systems_root = paths_module.SYSTEMS_ROOT

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    # 0. Preflight checks (run by default, skip with --no-preflight)
    if not skip_preflight and not dry_run:
        _progress("preflight", "Running preflight checks")
        preflight_result = run_preflight(executor, install_prefix=str(install_prefix))
        if not preflight_result.passed:
            # Collect fatal failure messages
            fatal_failures = [c for c in preflight_result.checks if c.fatal and not c.passed]
            details = "; ".join(f"{c.name}: {c.detail}" for c in fatal_failures)
            raise PreflightError(f"Preflight failed: {details}")
        if preflight_result.has_warnings:
            _progress("preflight_warn", "Preflight passed with warnings (non-fatal)")
        else:
            _progress("preflight_ok", "Preflight passed")
    elif skip_preflight:
        _progress("preflight", "Skipped (--no-preflight)")

    # 1. Load compat matrix
    _progress("compat", "Loading compatibility matrix")
    matrix = load_compat_matrix(compat_matrix_path)

    # 2. Resolve components
    _progress("resolve", "Resolving components")
    components = resolve_components(matrix)
    if not components:
        raise InstallerError("No components found in compatibility matrix")

    # Filter to only the providers used by the system or template
    if system_path is not None:
        system_providers = get_system_provider_names(system_path)
        if system_providers is not None:
            components = [c for c in components if c.name == "anolis" or c.binary_name in system_providers]
    else:
        template_providers = _get_template_provider_names(template_name)
        if template_providers is not None:
            components = [c for c in components if c.name == "anolis" or c.binary_name in template_providers]

    # 3. Detect platform
    _progress("platform", "Detecting platform")
    platform_str = detect_platform()

    # 4. Check existing binaries (Decision E)
    if not force:
        existing = check_existing_binaries(install_prefix, components, executor=executor)
        skip_components: list[ComponentSpec] = []
        install_components: list[ComponentSpec] = []

        for comp in components:
            existing_version = existing.get(comp.binary_name)
            if existing_version is not None and comp.version in existing_version:
                _progress("skip", f"{comp.binary_name} already at version {comp.version}")
                skip_components.append(comp)
            else:
                install_components.append(comp)

        if not install_components:
            _progress("skip_all", "All components already at correct versions")
            # Still provision the project if it doesn't exist
            project_dir_str = str(systems_root / project_name)
            if not executor.file_exists(project_dir_str):
                _progress("project", f"Creating project '{project_name}'")
                if not dry_run:
                    provision_project(
                        template_name,
                        project_name,
                        install_prefix,
                        force=force,
                        executor=executor,
                        systems_root=systems_root,
                        system_path=system_path,
                    )
            return InstallResult(
                components=components,
                install_prefix=install_prefix,
                project_path=systems_root / project_name,
                verified_versions={c.binary_name: c.version for c in components},
                dry_run=dry_run,
            )
        components = install_components

    # 5. Fetch manifests + download tarballs
    token = github_token or os.environ.get("GITHUB_TOKEN")
    session = requests.Session()
    tarballs: list[tuple[ComponentSpec, bytes]] = []

    for comp in components:
        _progress("manifest", f"Fetching manifest for {comp.name} v{comp.version}")
        manifest = fetch_manifest(session, comp.repo, comp.version, platform_str, token=token)

        _progress("download", f"Downloading {manifest.asset_name}")
        data = download_and_verify(session, manifest.download_url, manifest.sha256, token=token)
        _progress("verified", f"SHA256 verified: {manifest.asset_name}")
        tarballs.append((comp, data))

    # 6. Install tarballs
    if not dry_run:
        for comp, data in tarballs:
            _progress("install", f"Installing {comp.binary_name} to {install_prefix}")
            install_tarball(
                data,
                install_prefix,
                executor=executor,
                backup=True,
                binary_names=[comp.binary_name],
            )
    else:
        _progress("dry_run", "Skipping install (dry-run mode)")

    # 7. Verify installation
    if not dry_run:
        _progress("verify", "Verifying installed binaries")
        # Verify ALL components (including ones we skipped as already-installed)
        all_components = resolve_components(load_compat_matrix(compat_matrix_path))
        if system_path is not None:
            sys_providers = get_system_provider_names(system_path)
            if sys_providers is not None:
                all_components = [c for c in all_components if c.name == "anolis" or c.binary_name in sys_providers]
        else:
            tpl_providers = _get_template_provider_names(template_name)
            if tpl_providers is not None:
                all_components = [c for c in all_components if c.name == "anolis" or c.binary_name in tpl_providers]
        verified = verify_installation(install_prefix, all_components, executor=executor)
    else:
        verified = {c.binary_name: c.version for c in components}

    # 8. Create project
    _progress("project", f"Creating project '{project_name}'")
    if not dry_run:
        provision_project(
            template_name,
            project_name,
            install_prefix,
            force=force,
            executor=executor,
            systems_root=systems_root,
            system_path=system_path,
        )

    project_path = systems_root / project_name

    return InstallResult(
        components=components,
        install_prefix=install_prefix,
        project_path=project_path,
        verified_versions=verified,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_template_provider_names(template_name: str) -> set[str] | None:
    """Get the set of provider binary names used by a template.

    Returns None if the template cannot be loaded (caller should not filter).
    """
    tpl_path = paths_module.TEMPLATES_ROOT / template_name / "system.json"
    if not tpl_path.exists():
        return None
    try:
        system = json.loads(tpl_path.read_text(encoding="utf-8"))
        providers = system.get("paths", {}).get("providers", {})
        names: set[str] = set()
        for _pid, pdata in providers.items():
            exe = pdata.get("executable", "")
            if exe:
                names.add(Path(exe).name)
        return names
    except (json.JSONDecodeError, KeyError):
        return None


def get_system_provider_names(system_path: Path) -> set[str] | None:
    """Get the set of provider binary names from a custom system.json file.

    Returns None if the file cannot be loaded (caller should not filter).
    """
    if not system_path.exists():
        return None
    try:
        system = json.loads(system_path.read_text(encoding="utf-8"))
        providers = system.get("paths", {}).get("providers", {})
        names: set[str] = set()
        for _pid, pdata in providers.items():
            exe = pdata.get("executable", "")
            if exe:
                names.add(Path(exe).name)
        return names
    except (json.JSONDecodeError, KeyError):
        return None
