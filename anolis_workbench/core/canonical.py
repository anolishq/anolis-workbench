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
from pathlib import Path, PurePosixPath
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


MACHINE_ID_MAX_LEN = 64


def machine_id_from_name(name: str) -> str:
    """Slugify a project name into a profile machine_id.

    Must satisfy BOTH the machine-profile schema (`^[a-z0-9][a-z0-9-]*$`) and
    install.sh's project-path token class (`[a-z0-9-]+`) — workbench project
    names allow uppercase and underscores, so the project name must never be
    used in a config path. Capped in length because the value becomes a
    directory name (an over-long one fails with ENAMETOOLONG at write time).
    """
    lowered = name.strip().lower().replace("_", "-")
    cleaned = _MACHINE_ID_STRIP_RE.sub("-", lowered)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")[:MACHINE_ID_MAX_LEN].strip("-")
    # The strip above guarantees an alphanumeric first character, since the
    # substitution leaves only [a-z0-9-].
    return cleaned or "machine"


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


def command_kind(command: Any) -> str | None:
    """The provider kind install.sh will resolve from a command token.

    install.sh rewrites the token using the SECOND capture group and then
    re-derives the kind from the rendered `{prefix}/bin/anolis-provider-<kind>`
    path, so a token whose two kind captures disagree resolves to something
    other than what the filename implies. Returns None unless both agree.
    """
    if not isinstance(command, str):
        return None
    match = PROVIDER_COMMAND_RE.fullmatch(command)
    if match is None or match.group(1) != match.group(2):
        return None
    return match.group(2)


def _path_token_problem(machine_id: str, value: str, label: str) -> str | None:
    match = PROJECT_PATH_RE.fullmatch(value)
    if match is None:
        return f"{label} is not a canonical project path token: {value!r}"
    if match.group(1) != machine_id:
        return f"{label} names project '{match.group(1)}' but machine_id is '{machine_id}'"
    if ".." in PurePosixPath(match.group(3)).parts:
        return f"{label} escapes the project directory: {value!r}"
    return None


def assert_deploy_tokens(
    machine_id: str,
    runtime_doc: dict[str, Any],
    provider_kinds: dict[str, Any] | None = None,
) -> list[str]:
    """Problems that would make install.sh mis-render or refuse this variant.

    When `provider_kinds` is given, also checks that the kind install.sh
    resolves from each command token matches the kind the provider's config
    filename declares — install.sh's pinned-provider gate keys on the FORMER.
    """
    problems: list[str] = []
    for entry in runtime_doc.get("providers", []) or []:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("id", "<unknown>")
        command = entry.get("command")
        kind = command_kind(command)
        if kind is None:
            problems.append(
                f"provider '{pid}' command is not a canonical deploy token "
                f"(expected ../anolis-provider-<kind>/build/<preset>/anolis-provider-<kind> "
                f"with a consistent kind, got {command!r})"
            )
        elif provider_kinds is not None:
            declared = provider_kinds.get(pid)
            if isinstance(declared, str) and declared != kind:
                problems.append(
                    f"provider '{pid}' command resolves to kind '{kind}' but its config declares '{declared}'"
                )
        for arg in entry.get("args", []) or []:
            if not isinstance(arg, str):
                continue
            if arg.startswith("../"):
                problem = _path_token_problem(machine_id, arg, f"provider '{pid}' arg")
                if problem:
                    problems.append(problem)
            elif arg.endswith((".yaml", ".yml")):
                # A bare-relative config path is never rewritten by install.sh
                # and silently fails to resolve after install.
                problems.append(f"provider '{pid}' arg is a bare relative path install.sh will not rewrite: {arg!r}")
    automation = runtime_doc.get("automation")
    if isinstance(automation, dict):
        behavior = automation.get("behavior_tree")
        if isinstance(behavior, str) and behavior.startswith("../"):
            problem = _path_token_problem(machine_id, behavior, "automation.behavior_tree")
            if problem:
                problems.append(problem)
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

    Mirrors install.sh's inertness gates: automation enabled, or
    mode_transition_hooks present. install.sh REFUSES a non-inert `manual`
    variant at stage and install time, so the workbench must never author one.

    Deliberately errs strict: a non-mapping `automation` makes install.sh's
    stage-time renderer die outright, and a string like "false" is truthy to
    its Python gate, so both are violations here rather than silent passes.
    """
    if "automation" not in doc:
        return None
    automation = doc.get("automation")
    if automation is None:
        return None
    if not isinstance(automation, dict):
        return f"automation must be a mapping (install.sh fails to render {automation!r})"
    enabled = automation.get("enabled")
    if isinstance(enabled, str):
        return f"automation.enabled is the string {enabled!r} (install.sh treats any non-empty string as enabled)"
    if enabled:
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

    Raises when the result could not be schema-valid: the canonical contract has
    no representation of a zero-provider machine (both the machine-profile
    `compatibility.providers` and the runtime-config `providers` list require at
    least one entry), so a caller must seed a provider rather than persist a
    profile that only fails later at deploy.
    """
    if not providers:
        raise CanonicalError(
            "a canonical project needs at least one provider "
            "(machine-profile compatibility.providers and runtime-config providers are both non-empty)"
        )
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


def _checked_target(project_dir: Path, rel: Any, label: str) -> Path:
    """Resolve a profile-relative path, refusing anything that escapes.

    Without this, `project_dir / "../../x.yaml"` writes outside the workspace
    and `project_dir / "/etc/x"` discards the left side entirely — and profile
    data reaches this function from the HTTP API.
    """
    if not isinstance(rel, str):
        raise CanonicalError(f"{label} must be a string path, got {rel!r}")
    problem = machine_profile.containment_error(rel)
    if problem is not None:
        raise CanonicalError(f"{label} {problem}")
    return project_dir / rel


def write_project(project_dir: Path, document: dict[str, Any]) -> None:
    """Persist an AUTHORED canonical project.

    Refuses imported documents: those are carried verbatim (#226) and
    re-emitting them through the serializer would destroy comments and key
    order. Every referenced path is containment-checked before use.
    """
    if not document.get("authored"):
        raise CanonicalError(
            "refusing to write a project that is not workbench-authored — "
            "imported machine profiles are carried verbatim"
        )
    profile = document.get("profile")
    if not isinstance(profile, dict):
        raise CanonicalError("document.profile must be a mapping")

    runtime_profiles = profile.get("runtime_profiles")
    profile_providers = profile.get("providers")
    raw_variants = document.get("variants")
    variants: dict[str, Any] = raw_variants if isinstance(raw_variants, dict) else {}
    raw_providers = document.get("providers")
    providers: dict[str, Any] = raw_providers if isinstance(raw_providers, dict) else {}

    # Resolve every target BEFORE creating anything, so a bad reference cannot
    # leave a half-written project behind.
    targets: list[tuple[Path, Any]] = []
    if isinstance(runtime_profiles, dict):
        for variant, rel in runtime_profiles.items():
            doc = variants.get(variant)
            if isinstance(doc, dict):
                targets.append((_checked_target(project_dir, rel, f"runtime_profiles.{variant}"), doc))
    if isinstance(profile_providers, dict):
        for pid, entry in profile_providers.items():
            rel = entry.get("config") if isinstance(entry, dict) else None
            provider = providers.get(pid)
            config = provider.get("config") if isinstance(provider, dict) else None
            if isinstance(config, dict):
                targets.append((_checked_target(project_dir, rel, f"providers.{pid}.config"), config))

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / CONFIG_DIR).mkdir(exist_ok=True)
    _write_yaml_atomic(project_dir / machine_profile.PROFILE_FILENAME, profile)
    for path, doc in targets:
        _write_yaml_atomic(path, doc)

    _prune_orphans(project_dir, profile, {path.resolve() for path, _ in targets})


def _prune_orphans(project_dir: Path, profile: dict[str, Any], just_written: set[Path]) -> None:
    """Remove config files the profile no longer references (renamed/removed
    providers and variants), so the directory always matches the profile.

    `just_written` is unioned in so a file this same save produced can never be
    pruned by a path-normalization mismatch.
    """
    referenced = set(just_written) | {
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


def retarget_project(project_dir: Path, new_machine_id: str) -> None:
    """Re-key an authored project onto a new machine_id.

    Every `../anolis-projects/projects/<id>/...` token inside every config
    references the project directory by name, and install.sh keys its path
    rewrites on that basename. Copying a project without rewriting them would
    make the copy deploy into the ORIGINAL's directory — so create-from-template
    and duplicate both go through here.
    """
    profile = machine_profile.load_profile(project_dir)
    old_machine_id = profile.get("machine_id")
    if old_machine_id == new_machine_id:
        return

    pattern = re.compile(r"(\.\./anolis-projects/projects/)[a-z0-9-]+(/)")
    for rel in machine_profile.referenced_files(profile):
        if machine_profile.containment_error(rel) is not None:
            continue
        path = project_dir / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        rewritten = pattern.sub(rf"\g<1>{new_machine_id}\g<2>", text)
        if rewritten != text:
            _write_text_atomic(path, rewritten)

    profile["machine_id"] = new_machine_id
    _write_yaml_atomic(project_dir / machine_profile.PROFILE_FILENAME, profile)
