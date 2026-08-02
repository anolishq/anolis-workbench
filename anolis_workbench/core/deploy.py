"""Deploy a workbench project via the canonical anolis install.sh.

Deployment (/opt/anolis, `anolis` user, systemd) is delegated to the anolis
repo's install.sh — the single provisioning engine. Workbench's job is to
materialize a project config dir (machine-profile + configs + behaviors)
from the workspace and hand it over:

  local  : sudo bash install.sh --project <dir>
  remote : push <dir> + install.sh to the target, run the same there

Authoring / dev-launch (~/.anolis/systems, launcher.py) is unaffected.
"""

from __future__ import annotations

import logging
import pathlib
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

import requests
import yaml

from anolis_workbench.core import exporter, machine_profile, migrations, releases
from anolis_workbench.core import renderer as renderer_module
from anolis_workbench.core.executor import Executor, LocalExecutor
from anolis_workbench.core.paths import DEFAULT_INSTALL_PREFIX

logger = logging.getLogger(__name__)

# A real install downloads binaries and waits for systemd health.
INSTALL_TIMEOUT_S = 1800.0

ProgressCallback = Callable[[str, str], None]


class DeployError(RuntimeError):
    """Raised when a deployment cannot be materialized or install.sh fails."""


@dataclass
class MaterializedProject:
    """A project config dir in the install.sh layout."""

    project_dir: pathlib.Path
    runtime_version: str
    provider_kinds: dict[str, str]  # provider instance id -> kind


@dataclass
class DeployResult:
    project_name: str
    runtime_version: str
    prefix: str
    output: str


def materialize_project_dir(
    *,
    system: dict[str, Any],
    project_name: str,
    workspace_dir: pathlib.Path,
    dest: pathlib.Path,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
) -> MaterializedProject:
    """Render the workspace system into an install.sh project config dir.

    Layout (the install.sh --project contract):
      <dest>/<project_name>/machine-profile.yaml   (with components: pins)
      <dest>/<project_name>/config/anolis-runtime.manual.yaml
      <dest>/<project_name>/config/provider-<id>.yaml
      <dest>/<project_name>/behaviors/*.xml

    Configs are written production-ready (absolute <prefix> paths) —
    install.sh's render pass only rewrites dev-relative sibling-repo paths,
    which workbench-rendered configs never contain.
    """
    system, _ = migrations.migrate_system(system)  # defensive: tolerate a v1 doc from any caller

    project_dir = dest / project_name
    (project_dir / "config").mkdir(parents=True, exist_ok=True)

    rendered = renderer_module.render(system, project_name)
    runtime_yaml = rendered.get("anolis-runtime.yaml")
    if not isinstance(runtime_yaml, str) or runtime_yaml.strip() == "":
        raise DeployError("Renderer did not produce anolis-runtime.yaml")
    runtime_payload = yaml.safe_load(runtime_yaml) or {}
    if not isinstance(runtime_payload, dict):
        raise DeployError("Rendered runtime YAML root must be a mapping/object")

    # Provider entries: binary by kind, config by instance id — both absolute.
    topo_providers = system.get("topology", {}).get("providers", {})
    if not isinstance(topo_providers, dict):
        topo_providers = {}
    provider_ids: list[str] = []
    provider_kinds: dict[str, str] = {}
    for entry in runtime_payload.get("providers", []):
        pid = str(entry.get("id", "")).strip()
        if pid == "":
            raise DeployError("runtime.providers[].id must be a non-empty string")
        topo_entry = topo_providers.get(pid)
        kind = topo_entry.get("kind") if isinstance(topo_entry, dict) else None
        if not isinstance(kind, str) or kind == "":
            raise DeployError(
                f"provider {pid!r} has no kind in the system topology — "
                "deploy needs a released component to install for it"
            )
        provider_ids.append(pid)
        provider_kinds[pid] = kind
        entry["command"] = f"{prefix}/bin/anolis-provider-{kind}"
        args = [str(a) for a in entry.get("args", [])]
        cfg_path = f"{prefix}/config/providers/{pid}.yaml"
        for idx, token in enumerate(args[:-1]):
            if token == "--config":
                args[idx + 1] = cfg_path
                break
        else:
            args.extend(["--config", cfg_path])
        entry["args"] = args

    # Behavior tree: copy into behaviors/, point the config at the installed copy.
    behavior_names: list[str] = []
    automation = runtime_payload.get("automation")
    if isinstance(automation, dict):
        behavior_ref = automation.get("behavior_tree") or automation.get("behavior_tree_path")
        if isinstance(behavior_ref, str) and behavior_ref.strip() != "":
            source = workspace_dir / behavior_ref.strip()
            if not source.is_file():
                raise DeployError(f"Behavior tree file not found: {source}")
            (project_dir / "behaviors").mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, project_dir / "behaviors" / source.name)
            behavior_names.append(source.name)
            automation["behavior_tree"] = f"{prefix}/projects/{project_name}/behaviors/{source.name}"
            automation.pop("behavior_tree_path", None)

    (project_dir / "config" / "anolis-runtime.manual.yaml").write_text(
        yaml.safe_dump(runtime_payload, sort_keys=False), encoding="utf-8"
    )

    for pid in provider_ids:
        body = rendered.get(f"providers/{pid}.yaml")
        if not isinstance(body, str) or body.strip() == "":
            fallback = workspace_dir / "providers" / f"{pid}.yaml"
            if not fallback.is_file():
                raise DeployError(f"Provider config not found for {pid}")
            body = fallback.read_text(encoding="utf-8")
        (project_dir / "config" / f"provider-{pid}.yaml").write_text(body, encoding="utf-8")

    # Machine profile with components pins (shared exporter logic), paths
    # patched to this layout.
    profile = exporter._build_machine_profile(
        system=system,
        project_name=project_name,
        provider_ids=provider_ids,
        behavior_rel_paths={},
    )
    if "components" not in profile:
        raise DeployError(
            "could not resolve released component versions (offline or unreleased "
            "providers) — deploy requires pinned components"
        )
    profile["runtime_profiles"] = {"manual": "config/anolis-runtime.manual.yaml"}
    profile["providers"] = {pid: {"config": f"config/provider-{pid}.yaml"} for pid in provider_ids}
    if behavior_names:
        profile["behaviors"] = [f"behaviors/{name}" for name in sorted(behavior_names)]
    exporter._validate_machine_profile(profile)
    (project_dir / "machine-profile.yaml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")

    return MaterializedProject(
        project_dir=project_dir,
        runtime_version=profile["components"]["runtime"]["version"],
        provider_kinds=provider_kinds,
    )


def materialize_imported_project_dir(
    project_dir: pathlib.Path,
    dest: pathlib.Path,
    *,
    variant: str | None = None,
) -> MaterializedProject:
    """Materialize an imported (machine-profile) project for install.sh (#226).

    Near-passthrough: every file is copied BYTE-FOR-BYTE — no rendering, no
    profile synthesis, no network. install.sh natively understands this layout
    (components pins, provider-<kind> configs, runtime_profiles variants,
    dev-relative path rewrites), so the workbench's whole job is an honest copy
    plus fast fail-early validation.
    """
    project_dir = project_dir.resolve()
    try:
        profile = machine_profile.load_profile(project_dir)
    except machine_profile.ProfileError as exc:
        raise DeployError(str(exc)) from exc
    errors = machine_profile.schema_errors(profile)
    if errors:
        raise DeployError(f"machine-profile failed schema validation: {'; '.join(errors)}")

    components = profile.get("components")
    if not isinstance(components, dict):
        raise DeployError(
            "machine profile has no components block — deploy requires pinned "
            "components (fill components.runtime/providers before deploying)"
        )
    runtime_component = components.get("runtime")
    runtime_version = runtime_component.get("version") if isinstance(runtime_component, dict) else None
    if not isinstance(runtime_version, str) or runtime_version == "":
        raise DeployError("machine profile components.runtime.version is missing")

    runtime_profiles = profile.get("runtime_profiles")
    if not isinstance(runtime_profiles, dict) or not isinstance(runtime_profiles.get("manual"), str):
        raise DeployError("machine profile has no 'manual' runtime profile — boot-inert install requires one")
    if variant is not None and variant not in runtime_profiles:
        known = ", ".join(sorted(runtime_profiles))
        raise DeployError(f"variant '{variant}' not in the profile's runtime_profiles (known: {known})")

    uncarriable = [
        f"{ref} ({problem})"
        for ref in machine_profile.referenced_files(profile)
        if (problem := machine_profile.containment_error(ref)) is not None
    ]
    if uncarriable:
        raise DeployError(f"machine profile references files it cannot carry: {', '.join(sorted(uncarriable))}")
    missing = [
        ref
        for ref in machine_profile.referenced_files(profile)
        if machine_profile.containment_error(ref) is None and not (project_dir / ref).is_file()
    ]
    if missing:
        raise DeployError(f"machine profile references missing files: {', '.join(sorted(missing))}")

    # install.sh keys its ../anolis-projects/projects/<name>/ path rewrites on
    # the project dir BASENAME — use the original canonical dir name, not the
    # (renamable) workbench project name.
    sidecar = machine_profile.read_sidecar(project_dir) or {}
    raw_meta = sidecar.get("meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    profile_dir_name = str(meta.get("profile_dir_name") or profile.get("machine_id") or project_dir.name)
    # This value builds a filesystem path; the sidecar is workbench-owned but
    # unvalidated, so keep it to a single plain directory name.
    if profile_dir_name in ("", ".", "..") or "/" in profile_dir_name or "\\" in profile_dir_name:
        raise DeployError(f"invalid project directory name {profile_dir_name!r} in the imported project sidecar")

    out = dest / profile_dir_name
    out.mkdir(parents=True, exist_ok=True)
    for entry in machine_profile.copy_entries(profile):
        src = project_dir / entry
        if src.is_file():
            shutil.copy2(src, out / entry)
        elif src.is_dir():
            shutil.copytree(src, out / entry, copy_function=shutil.copy2, dirs_exist_ok=True)

    provider_kinds = {
        pid: kind or "unknown" for pid, kind in machine_profile.derive_kinds(profile, project_dir).items()
    }
    return MaterializedProject(
        project_dir=out,
        runtime_version=runtime_version,
        provider_kinds=provider_kinds,
    )


def fetch_install_sh(runtime_version: str, dest: pathlib.Path) -> pathlib.Path:
    """Download install.sh from the anolis release the profile pins."""
    url = f"https://github.com/{releases.RUNTIME_REPO}/releases/download/v{runtime_version}/install.sh"
    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException as exc:
        raise DeployError(f"failed to download install.sh from {url}: {exc}") from exc
    if resp.status_code != 200:
        raise DeployError(f"failed to download install.sh from {url}: HTTP {resp.status_code}")
    path = dest / "install.sh"
    path.write_bytes(resp.content)
    path.chmod(0o755)
    return path


def run_rollback(
    executor: Executor | None = None,
    *,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    staging: str = "/tmp/anolis-deploy",
) -> str:
    """Restore the previous binaries via `install.sh --rollback` on the target.

    Rollback is version-independent (it swaps <prefix>/.prev back), so the
    latest released install.sh is used. The script is staged via the executor,
    which makes the same path work locally and over SSH.
    """
    if executor is None:
        executor = LocalExecutor()

    version = releases.latest_release_version(releases.RUNTIME_REPO)
    if version is None:
        raise DeployError("could not resolve the latest anolis release (offline?)")

    with tempfile.TemporaryDirectory(prefix="anolis-rollback-") as td:
        install_sh = fetch_install_sh(version, pathlib.Path(td))
        target_path = f"{staging}/install.sh"
        executor.mkdir(staging)
        executor.write_file(target_path, install_sh.read_bytes())

    args = ["--rollback"]
    if pathlib.Path(prefix) != DEFAULT_INSTALL_PREFIX:
        args += ["--prefix", str(prefix)]
    result = executor.run(["bash", target_path, *args], sudo=True, timeout=300)
    if result.returncode != 0:
        tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-10:])
        raise DeployError(f"install.sh --rollback failed (exit {result.returncode}):\n{tail}")
    return result.stdout


def _install_args(
    project_dir: str,
    *,
    prefix: pathlib.Path,
    no_start: bool,
    dry_run: bool,
    with_telemetry_export: bool = False,
    variant: str | None = None,
) -> list[str]:
    args = ["--project", project_dir]
    if pathlib.Path(prefix) != DEFAULT_INSTALL_PREFIX:
        args += ["--prefix", str(prefix)]
    if variant is not None:
        args += ["--variant", variant]
    if no_start:
        args.append("--no-start")
    if dry_run:
        args.append("--dry-run")
    # Telemetry-export provisioning is owned by install.sh (anolishq/anolis#137);
    # workbench just requests it — the service is installed on the target, not
    # on the operator's host.
    if with_telemetry_export:
        args.append("--with-telemetry-export")
    return args


def _push_dir(executor: Executor, local_dir: pathlib.Path, remote_root: str) -> None:
    for local in sorted(p for p in local_dir.rglob("*") if p.is_file()):
        rel = local.relative_to(local_dir).as_posix()
        remote_path = f"{remote_root}/{rel}"
        executor.mkdir(remote_path.rsplit("/", 1)[0])
        executor.write_file(remote_path, local.read_bytes())


def _run_install_sh(
    executor: Executor,
    install_sh: str,
    args: list[str],
    progress: ProgressCallback | None,
) -> str:
    result = executor.run(["bash", install_sh, *args], sudo=True, timeout=INSTALL_TIMEOUT_S)
    if result.returncode != 0:
        tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-15:])
        raise DeployError(f"install.sh failed (exit {result.returncode}):\n{tail}")
    if progress:
        progress("install", "install.sh completed")
    return result.stdout


def deploy_local(
    *,
    system: dict[str, Any],
    project_name: str,
    workspace_dir: pathlib.Path,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    no_start: bool = False,
    dry_run: bool = False,
    with_telemetry_export: bool = False,
    executor: Executor | None = None,
    progress_callback: ProgressCallback | None = None,
) -> DeployResult:
    """Materialize the project config and run install.sh --project locally."""
    if executor is None:
        executor = LocalExecutor()

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    with tempfile.TemporaryDirectory(prefix="anolis-deploy-") as td:
        tmp = pathlib.Path(td)
        _progress("materialize", "Rendering project config for install.sh")
        mat = materialize_project_dir(
            system=system,
            project_name=project_name,
            workspace_dir=workspace_dir,
            dest=tmp,
            prefix=prefix,
        )
        _progress("fetch", f"Fetching install.sh v{mat.runtime_version}")
        install_sh = fetch_install_sh(mat.runtime_version, tmp)
        _progress("install", f"Running install.sh --project (runtime v{mat.runtime_version})")
        output = _run_install_sh(
            executor,
            str(install_sh),
            _install_args(
                str(mat.project_dir),
                prefix=prefix,
                no_start=no_start,
                dry_run=dry_run,
                with_telemetry_export=with_telemetry_export,
            ),
            progress_callback,
        )
    return DeployResult(
        project_name=project_name,
        runtime_version=mat.runtime_version,
        prefix=str(prefix),
        output=output,
    )


def deploy_remote(
    *,
    executor: Executor,
    system: dict[str, Any],
    project_name: str,
    workspace_dir: pathlib.Path,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    no_start: bool = False,
    dry_run: bool = False,
    with_telemetry_export: bool = False,
    remote_staging: str = "/tmp/anolis-deploy",
    progress_callback: ProgressCallback | None = None,
) -> DeployResult:
    """Materialize locally, push the config dir + install.sh, run on the target.

    The target runs `install.sh --project`, so it needs network access plus
    python3/pyyaml (install.sh checks and reports what is missing).
    """

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    with tempfile.TemporaryDirectory(prefix="anolis-deploy-") as td:
        tmp = pathlib.Path(td)
        _progress("materialize", "Rendering project config for install.sh")
        mat = materialize_project_dir(
            system=system,
            project_name=project_name,
            workspace_dir=workspace_dir,
            dest=tmp,
            prefix=prefix,
        )
        install_sh = fetch_install_sh(mat.runtime_version, tmp)

        remote_root = f"{remote_staging}/{project_name}"
        _progress("push", f"Pushing project config to {remote_root}")
        _push_dir(executor, mat.project_dir, remote_root)
        remote_install = f"{remote_staging}/install.sh"
        executor.write_file(remote_install, install_sh.read_bytes())

        _progress("install", f"Running install.sh --project on target (runtime v{mat.runtime_version})")
        output = _run_install_sh(
            executor,
            remote_install,
            _install_args(
                remote_root,
                prefix=prefix,
                no_start=no_start,
                dry_run=dry_run,
                with_telemetry_export=with_telemetry_export,
            ),
            progress_callback,
        )
    return DeployResult(
        project_name=project_name,
        runtime_version=mat.runtime_version,
        prefix=str(prefix),
        output=output,
    )


def deploy_local_profile(
    *,
    project_dir: pathlib.Path,
    project_name: str,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    no_start: bool = False,
    dry_run: bool = False,
    with_telemetry_export: bool = False,
    variant: str | None = None,
    executor: Executor | None = None,
    progress_callback: ProgressCallback | None = None,
) -> DeployResult:
    """Copy an imported machine-profile project verbatim and run install.sh --project."""
    if executor is None:
        executor = LocalExecutor()

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    with tempfile.TemporaryDirectory(prefix="anolis-deploy-") as td:
        tmp = pathlib.Path(td)
        _progress("materialize", "Copying machine-profile project for install.sh")
        mat = materialize_imported_project_dir(project_dir, tmp, variant=variant)
        _progress("fetch", f"Fetching install.sh v{mat.runtime_version}")
        install_sh = fetch_install_sh(mat.runtime_version, tmp)
        _progress("install", f"Running install.sh --project (runtime v{mat.runtime_version})")
        output = _run_install_sh(
            executor,
            str(install_sh),
            _install_args(
                str(mat.project_dir),
                prefix=prefix,
                no_start=no_start,
                dry_run=dry_run,
                with_telemetry_export=with_telemetry_export,
                variant=variant,
            ),
            progress_callback,
        )
    return DeployResult(
        project_name=project_name,
        runtime_version=mat.runtime_version,
        prefix=str(prefix),
        output=output,
    )


def deploy_remote_profile(
    *,
    executor: Executor,
    project_dir: pathlib.Path,
    project_name: str,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    no_start: bool = False,
    dry_run: bool = False,
    with_telemetry_export: bool = False,
    variant: str | None = None,
    remote_staging: str = "/tmp/anolis-deploy",
    progress_callback: ProgressCallback | None = None,
) -> DeployResult:
    """Copy an imported machine-profile project verbatim, push it, run install.sh remotely."""

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    with tempfile.TemporaryDirectory(prefix="anolis-deploy-") as td:
        tmp = pathlib.Path(td)
        _progress("materialize", "Copying machine-profile project for install.sh")
        mat = materialize_imported_project_dir(project_dir, tmp, variant=variant)
        install_sh = fetch_install_sh(mat.runtime_version, tmp)

        remote_root = f"{remote_staging}/{mat.project_dir.name}"
        _progress("push", f"Pushing project config to {remote_root}")
        _push_dir(executor, mat.project_dir, remote_root)
        remote_install = f"{remote_staging}/install.sh"
        executor.write_file(remote_install, install_sh.read_bytes())

        _progress("install", f"Running install.sh --project on target (runtime v{mat.runtime_version})")
        output = _run_install_sh(
            executor,
            remote_install,
            _install_args(
                remote_root,
                prefix=prefix,
                no_start=no_start,
                dry_run=dry_run,
                with_telemetry_export=with_telemetry_export,
                variant=variant,
            ),
            progress_callback,
        )
    return DeployResult(
        project_name=project_name,
        runtime_version=mat.runtime_version,
        prefix=str(prefix),
        output=output,
    )


def stage_bundle_profile(
    *,
    project_dir: pathlib.Path,
    project_name: str,
    out_dir: pathlib.Path,
    arch: str | None = None,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    variant: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    """Build an offline bundle from an imported machine-profile project via install.sh --stage."""

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_glob = "anolis-*.tar.gz"
    before = {p.name for p in out_dir.glob(bundle_glob)}
    with tempfile.TemporaryDirectory(prefix="anolis-stage-") as td:
        tmp = pathlib.Path(td)
        _progress("materialize", "Copying machine-profile project for install.sh --stage")
        mat = materialize_imported_project_dir(project_dir, tmp, variant=variant)
        install_sh = fetch_install_sh(mat.runtime_version, tmp)
        cmd = ["bash", str(install_sh), "--stage", str(out_dir), "--project", str(mat.project_dir)]
        if pathlib.Path(prefix) != DEFAULT_INSTALL_PREFIX:
            cmd += ["--prefix", str(prefix)]
        if variant:
            cmd += ["--variant", variant]
        if arch:
            cmd += ["--arch", arch]
        _progress("stage", f"Building offline bundle (runtime v{mat.runtime_version})")
        result = LocalExecutor().run(cmd, timeout=INSTALL_TIMEOUT_S)
        if result.returncode != 0:
            tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-15:])
            raise DeployError(f"install.sh --stage failed (exit {result.returncode}):\n{tail}")
    produced = [p for p in out_dir.glob(bundle_glob) if p.name not in before]
    if not produced:
        raise DeployError(f"install.sh --stage produced no bundle tarball in {out_dir}")
    return max(produced, key=lambda p: p.stat().st_mtime)


def stage_bundle(
    *,
    system: dict[str, Any],
    project_name: str,
    workspace_dir: pathlib.Path,
    out_dir: pathlib.Path,
    arch: str | None = None,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    """Build an offline bundle tarball via install.sh --stage (no root).

    Returns the tarball path in out_dir.
    """

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_glob = f"anolis-{project_name}-*.tar.gz"
    before = {p.name for p in out_dir.glob(bundle_glob)}
    with tempfile.TemporaryDirectory(prefix="anolis-stage-") as td:
        tmp = pathlib.Path(td)
        _progress("materialize", "Rendering project config for install.sh --stage")
        mat = materialize_project_dir(
            system=system,
            project_name=project_name,
            workspace_dir=workspace_dir,
            dest=tmp,
            prefix=prefix,
        )
        install_sh = fetch_install_sh(mat.runtime_version, tmp)
        cmd = ["bash", str(install_sh), "--stage", str(out_dir), "--project", str(mat.project_dir)]
        if pathlib.Path(prefix) != DEFAULT_INSTALL_PREFIX:
            cmd += ["--prefix", str(prefix)]
        if arch:
            cmd += ["--arch", arch]
        _progress("stage", f"Building offline bundle (runtime v{mat.runtime_version})")
        result = LocalExecutor().run(cmd, timeout=INSTALL_TIMEOUT_S)
        if result.returncode != 0:
            tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-15:])
            raise DeployError(f"install.sh --stage failed (exit {result.returncode}):\n{tail}")
    produced = [p for p in out_dir.glob(bundle_glob) if p.name not in before]
    if not produced:
        raise DeployError(f"install.sh --stage produced no bundle tarball in {out_dir}")
    return max(produced, key=lambda p: p.stat().st_mtime)
