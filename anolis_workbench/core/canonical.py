"""Authoring the canonical project artifacts (#255).

`machine_profile.py` owns the READ side of a canonical project (load, schema
validation, containment, kind derivation). This module owns the WRITE side:
building and persisting the machine-profile, the runtime-config variants, and
the provider configs that the workbench authors itself.

The one idea to keep in mind while reading this file: inside a canonical
runtime config, a provider `command` is **not a host path** — it is a deploy
TOKEN that install.sh rewrites to `{prefix}/bin/anolis-provider-<kind>`. The
host's real binary path (which on Windows does not even resemble the token)
lives only in the workbench sidecar, and is projected into a throwaway launch
config for dev-launch. That separation is what lets a single set of artifacts
serve both `install.sh` deploys and local dev-launch.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from anolis_workbench.core import machine_profile
from anolis_workbench.core.machine_profile import FORMAT_MACHINE_PROFILE

CONFIG_DIR = "config"
BEHAVIORS_DIR = "behaviors"
LAUNCH_DIR = ".workbench"

MANUAL_VARIANT = "manual"
AUTOMATION_VARIANT = "automation"

SIDECAR_SCHEMA_VERSION = 2

# The build preset baked into an authored provider command token. install.sh
# accepts any single path segment here ([^/]+) and rewrites the whole token, so
# this only has to be a plausible dev-checkout preset.
DEFAULT_BUILD_PRESET = "dev-release"

_MACHINE_ID_STRIP_RE = re.compile(r"[^a-z0-9-]+")

# Mirrors of the two install.sh rewrite patterns (tools/install.sh render pass).
# Authored tokens MUST match these or a deploy silently ships dev-relative
# paths — and the pinned-provider cross-validation hard-fails.
PROVIDER_COMMAND_RE = re.compile(r"\.\./anolis-provider-([a-z0-9-]+)/build/[^/]+/anolis-provider-([a-z0-9-]+)")
PROJECT_PATH_RE = re.compile(r"\.\./anolis-projects/projects/([a-z0-9-]+)/(config|behaviors)/([^\s\"']+)")

_RUNTIME_SCHEMA_CACHE: dict[str, Any] | None = None


class CanonicalError(ValueError):
    """Raised when canonical artifacts cannot be built or written."""


# ---------------------------------------------------------------------------
# Identity + path tokens
# ---------------------------------------------------------------------------


def machine_id_from_name(name: str) -> str:
    """Slugify a project name into a profile machine_id.

    Must satisfy BOTH the machine-profile schema (`^[a-z0-9][a-z0-9-]*$`) and
    install.sh's project-path token class (`[a-z0-9-]+`) — workbench project
    names allow uppercase and underscores, so the project name must never be
    used in a config path.
    """
    lowered = name.strip().lower().replace("_", "-")
    cleaned = _MACHINE_ID_STRIP_RE.sub("-", lowered)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if cleaned == "":
        cleaned = "machine"
    if not cleaned[0].isalnum():
        cleaned = f"m-{cleaned}"
    return cleaned


def variant_filename(variant: str) -> str:
    return f"anolis-runtime.{variant}.yaml"


def variant_relpath(variant: str) -> str:
    return f"{CONFIG_DIR}/{variant_filename(variant)}"


def provider_config_filename(kind: str, provider_id: str) -> str:
    """`provider-<kind>.<pid>.yaml`.

    install.sh derives the installed provider config name by taking the stem up
    to the FIRST dot, so the kind must come first for its glob to resolve.
    """
    return f"provider-{kind}.{provider_id}.yaml"


def provider_config_relpath(kind: str, provider_id: str) -> str:
    return f"{CONFIG_DIR}/{provider_config_filename(kind, provider_id)}"


def provider_command_token(kind: str, preset: str = DEFAULT_BUILD_PRESET) -> str:
    """The deploy token install.sh rewrites to `{prefix}/bin/anolis-provider-<kind>`."""
    return f"../anolis-provider-{kind}/build/{preset}/anolis-provider-{kind}"


def project_path_token(machine_id: str, relpath: str) -> str:
    """A `../anolis-projects/projects/<machine_id>/<config|behaviors>/...` token."""
    return f"../anolis-projects/projects/{machine_id}/{relpath}"


def assert_deploy_tokens(machine_id: str, runtime_doc: dict[str, Any]) -> list[str]:
    """Problems that would make install.sh mis-render or refuse this variant."""
    problems: list[str] = []
    for entry in runtime_doc.get("providers", []) or []:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("id", "<unknown>")
        command = entry.get("command")
        if not isinstance(command, str) or not PROVIDER_COMMAND_RE.fullmatch(command):
            problems.append(
                f"provider '{pid}' command is not a canonical deploy token "
                f"(expected ../anolis-provider-<kind>/build/<preset>/anolis-provider-<kind>, got {command!r})"
            )
        for arg in entry.get("args", []) or []:
            if not isinstance(arg, str) or not arg.startswith("../"):
                continue
            match = PROJECT_PATH_RE.fullmatch(arg)
            if match is None:
                problems.append(f"provider '{pid}' config arg is not a canonical project path token: {arg!r}")
            elif match.group(1) != machine_id:
                problems.append(
                    f"provider '{pid}' config arg names project '{match.group(1)}' but machine_id is '{machine_id}'"
                )
    automation = runtime_doc.get("automation")
    if isinstance(automation, dict):
        behavior = automation.get("behavior_tree")
        if isinstance(behavior, str) and behavior.startswith("../"):
            match = PROJECT_PATH_RE.fullmatch(behavior)
            if match is None:
                problems.append(f"automation.behavior_tree is not a canonical project path token: {behavior!r}")
            elif match.group(1) != machine_id:
                problems.append(
                    f"automation.behavior_tree names project '{match.group(1)}' but machine_id is '{machine_id}'"
                )
    return problems


# ---------------------------------------------------------------------------
# Runtime-config validation + inertness
# ---------------------------------------------------------------------------


def _runtime_schema() -> dict[str, Any]:
    global _RUNTIME_SCHEMA_CACHE
    if _RUNTIME_SCHEMA_CACHE is None:
        schema_file = resources.files("anolis_workbench").joinpath("schemas").joinpath("runtime-config.schema.json")
        payload = json.loads(schema_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CanonicalError("Bundled runtime-config schema root must be an object")
        _RUNTIME_SCHEMA_CACHE = payload
    return _RUNTIME_SCHEMA_CACHE


def runtime_config_errors(doc: Any) -> list[str]:
    """Validate a runtime-config document against the vendored schema."""
    validator = jsonschema.Draft7Validator(_runtime_schema())
    return [
        f"{'.'.join(str(p) for p in err.path) or '$'}: {err.message}"
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    ]


def inertness_violation(doc: dict[str, Any]) -> str | None:
    """Why this runtime config is not inert, or None.

    Mirrors install.sh's `_automation_inert_violation`: automation enabled, or
    mode_transition_hooks present. install.sh REFUSES a non-inert `manual`
    variant at stage and install time, so the workbench must never author one.
    """
    automation = doc.get("automation")
    if not isinstance(automation, dict):
        return None
    if automation.get("enabled"):
        return "automation.enabled is true"
    if "mode_transition_hooks" in automation:
        return "automation.mode_transition_hooks is present"
    return None


def pinned_kinds(profile: dict[str, Any]) -> set[str]:
    components = profile.get("components")
    if not isinstance(components, dict):
        return set()
    providers = components.get("providers")
    return set(providers.keys()) if isinstance(providers, dict) else set()


# ---------------------------------------------------------------------------
# Building the profile
# ---------------------------------------------------------------------------


def build_profile(
    *,
    machine_id: str,
    display_name: str,
    providers: dict[str, str],
    variants: dict[str, str] | None = None,
    behaviors: list[str] | None = None,
    components: dict[str, Any] | None = None,
    compatibility: dict[str, Any] | None = None,
    safety: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a machine-profile document.

    `providers` maps provider id -> its config relpath; `variants` maps variant
    name -> its config relpath (defaults to just `manual`). Pins (`components`)
    are AUTHORED data — this never invents them from release lookups.
    """
    profile: dict[str, Any] = {
        "schema_version": 1,
        "machine_id": machine_id,
        "display_name": display_name,
        "runtime_profiles": dict(variants or {MANUAL_VARIANT: variant_relpath(MANUAL_VARIANT)}),
        "providers": {pid: {"config": rel} for pid, rel in providers.items()},
    }
    if behaviors:
        profile["behaviors"] = list(behaviors)
    if safety:
        profile["safety"] = dict(safety)
    profile["compatibility"] = compatibility or {
        "runtime": {"config_contract": "01-runtime-config", "http_contract": "02-runtime-http"},
        "providers": {pid: {"strategy": "local-build", "version": "unspecified"} for pid in providers},
    }
    if components:
        profile["components"] = components
    return profile


def default_sidecar(
    *,
    name: str,
    created: str,
    machine_id: str,
    template: str | None = None,
    host_paths: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """The workbench-owned sidecar for an AUTHORED canonical project."""
    meta: dict[str, Any] = {"name": name, "created": created, "profile_dir_name": machine_id}
    if template:
        meta["template"] = template
    return {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "format": FORMAT_MACHINE_PROFILE,
        "authored": True,
        "meta": meta,
        "host_paths": host_paths or {"runtime_executable": "", "providers": {}},
        "launch": {"variant": MANUAL_VARIANT},
        "warnings": list(warnings or []),
    }


def is_authored(sidecar: dict[str, Any] | None) -> bool:
    """Authored projects are rewritable; imported ones are carried verbatim.

    Sidecars written before #255 have no `authored` key and are always imports.
    """
    return bool(sidecar.get("authored")) if isinstance(sidecar, dict) else False


def host_paths(sidecar: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(sidecar, dict):
        return {"runtime_executable": "", "providers": {}}
    raw = sidecar.get("host_paths")
    if not isinstance(raw, dict):
        return {"runtime_executable": "", "providers": {}}
    providers = raw.get("providers")
    return {
        "runtime_executable": raw.get("runtime_executable") or "",
        "providers": providers if isinstance(providers, dict) else {},
    }


# ---------------------------------------------------------------------------
# Reading / writing a canonical project
# ---------------------------------------------------------------------------


def read_project(project_dir: Path) -> dict[str, Any]:
    """The whole canonical project as one document (parsed files, not a model).

    Because this carries the FULL parsed documents rather than a workbench
    model of them, keys the workbench does not understand round-trip intact.
    """
    profile = machine_profile.load_profile(project_dir)
    sidecar = machine_profile.read_sidecar(project_dir) or {}

    variants: dict[str, Any] = {}
    runtime_profiles = profile.get("runtime_profiles")
    if isinstance(runtime_profiles, dict):
        for variant, rel in runtime_profiles.items():
            if not isinstance(rel, str):
                continue
            path = project_dir / rel
            if path.is_file():
                variants[variant] = _load_yaml(path)

    providers: dict[str, Any] = {}
    kinds = machine_profile.derive_kinds(profile, project_dir)
    profile_providers = profile.get("providers")
    if isinstance(profile_providers, dict):
        for pid, entry in profile_providers.items():
            rel = entry.get("config") if isinstance(entry, dict) else None
            config: Any = {}
            if isinstance(rel, str) and (project_dir / rel).is_file():
                config = _load_yaml(project_dir / rel)
            providers[pid] = {"kind": kinds.get(pid), "config": config}

    return {
        "format": "machine-profile",
        "authored": is_authored(sidecar),
        "meta": sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {},
        "profile": profile,
        "variants": variants,
        "providers": providers,
        "host_paths": host_paths(sidecar),
        "launch": sidecar.get("launch") if isinstance(sidecar.get("launch"), dict) else {},
        "warnings": sidecar.get("warnings") if isinstance(sidecar.get("warnings"), list) else [],
    }


def write_project(project_dir: Path, document: dict[str, Any]) -> None:
    """Persist an authored canonical project.

    Writes every artifact atomically. Callers must have validated first; this
    function does not decide policy, it only serializes.
    """
    profile = document.get("profile")
    if not isinstance(profile, dict):
        raise CanonicalError("document.profile must be a mapping")

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / CONFIG_DIR).mkdir(exist_ok=True)

    _write_yaml_atomic(project_dir / machine_profile.PROFILE_FILENAME, profile)

    runtime_profiles = profile.get("runtime_profiles")
    variants = document.get("variants") or {}
    if isinstance(runtime_profiles, dict) and isinstance(variants, dict):
        for variant, rel in runtime_profiles.items():
            doc = variants.get(variant)
            if isinstance(doc, dict) and isinstance(rel, str):
                _write_yaml_atomic(project_dir / rel, doc)

    profile_providers = profile.get("providers")
    providers = document.get("providers") or {}
    if isinstance(profile_providers, dict) and isinstance(providers, dict):
        for pid, entry in profile_providers.items():
            rel = entry.get("config") if isinstance(entry, dict) else None
            provider = providers.get(pid)
            config = provider.get("config") if isinstance(provider, dict) else None
            if isinstance(rel, str) and isinstance(config, dict):
                _write_yaml_atomic(project_dir / rel, config)

    _prune_orphans(project_dir, profile)


def _prune_orphans(project_dir: Path, profile: dict[str, Any]) -> None:
    """Remove config files the profile no longer references (renamed/removed
    providers and variants), so the directory always matches the profile."""
    referenced = {
        (project_dir / ref).resolve()
        for ref in machine_profile.referenced_files(profile)
        if machine_profile.containment_error(ref) is None
    }
    config_dir = project_dir / CONFIG_DIR
    if not config_dir.is_dir():
        return
    for path in config_dir.glob("*.yaml"):
        if path.resolve() not in referenced:
            path.unlink()


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise CanonicalError(f"Failed reading {path}: {exc}") from exc


def _write_yaml_atomic(path: Path, document: Any) -> None:
    # sort_keys=False keeps authored key order; default_flow_style=False keeps
    # `bind: 127.0.0.1` unquoted, which install.sh's LAN-exposure rewrite needs.
    text = yaml.safe_dump(document, default_flow_style=False, sort_keys=False)
    _write_text_atomic(path, text)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_sidecar(project_dir: Path, sidecar: dict[str, Any]) -> None:
    _write_text_atomic(project_dir / machine_profile.SIDECAR_NAME, json.dumps(sidecar, indent=2) + "\n")
