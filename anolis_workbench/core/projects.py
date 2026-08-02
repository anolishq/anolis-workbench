"""Project CRUD and filesystem helpers for Anolis Workbench."""

import json
import os
import pathlib
import re
import shutil
from datetime import datetime, timezone

import jsonschema

from anolis_workbench.core import machine_profile, migrations, provider_schemas, renderer
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import validator as semantic_validator

SYSTEMS_ROOT = paths_module.SYSTEMS_ROOT
TEMPLATES_ROOT = paths_module.TEMPLATES_ROOT
SYSTEM_SCHEMA_PATH = paths_module.SYSTEM_SCHEMA_PATH

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_SYSTEM_SCHEMA_CACHE: dict | None = None

SIDECAR_NAME = machine_profile.SIDECAR_NAME

FORMAT_SYSTEM = "system"
FORMAT_MACHINE_PROFILE = "machine-profile"

# Only these are copied on import (issue #226): the profile plus everything it
# can reference. docs/, config-release/, workbench/ etc. stay in the source repo,
# and import REFUSES a profile whose references fall outside this set.
_IMPORT_COPY = machine_profile.IMPORT_COPY


class ProjectValidationError(ValueError):
    """Raised when a composer system document fails validation."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("Project validation failed")
        self.errors = errors


class ImportValidationError(ValueError):
    """Raised when a machine-profile directory fails import validation."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("Import validation failed")
        self.errors = errors


class ReadOnlyProjectError(ValueError):
    """Raised when a write path is attempted on an imported (verbatim) project."""


def validate_name(name: str) -> "str | None":
    if not NAME_RE.match(name or ""):
        return "Project name must be 1-64 characters: letters, digits, hyphens, underscores only."
    return None


def _json_path_from_iter(path_parts: list) -> str:
    if not path_parts:
        return "$"
    out = "$"
    for part in path_parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}"
    return out


def _load_system_schema() -> dict:
    global _SYSTEM_SCHEMA_CACHE
    if _SYSTEM_SCHEMA_CACHE is None:
        payload = json.loads(SYSTEM_SCHEMA_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid system schema at {SYSTEM_SCHEMA_PATH}: root must be object")
        _SYSTEM_SCHEMA_CACHE = payload
    return _SYSTEM_SCHEMA_CACHE


def validate_system_payload(system: object) -> list[dict[str, str]]:
    """Return structured validation errors for a system document."""
    if not isinstance(system, dict):
        return [
            {
                "source": "schema",
                "code": "schema.type",
                "path": "$",
                "message": "system payload must be a JSON object",
            }
        ]

    schema = _load_system_schema()
    schema_validator = jsonschema.Draft7Validator(schema)
    schema_errors = sorted(schema_validator.iter_errors(system), key=lambda err: list(err.path))
    if schema_errors:
        return [
            {
                "source": "schema",
                "code": "schema.validation",
                "path": _json_path_from_iter(list(err.path)),
                "message": err.message,
            }
            for err in schema_errors
        ]

    errors = _validate_provider_configs(system)
    semantic_messages = semantic_validator.validate_system(system)
    errors.extend(
        {
            "source": "semantic",
            "code": "semantic.validation",
            "path": "$",
            "message": msg,
        }
        for msg in semantic_messages
    )
    return errors


def _validate_provider_configs(system: dict) -> list[dict[str, str]]:
    """Validate each provider's config against its vendored --config-schema
    envelope (Draft 2020-12) plus the x-anolis-unique annotations."""
    errors: list[dict[str, str]] = []
    providers = system.get("topology", {}).get("providers", {})
    if not isinstance(providers, dict):
        return errors

    for pid, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        base_path = f"$.topology.providers.{pid}"
        envelope = provider_schemas.get_envelope(kind) if isinstance(kind, str) else None
        if envelope is None:
            known = ", ".join(provider_schemas.available_kinds())
            errors.append(
                {
                    "source": "provider-schema",
                    "code": "provider.unknown_kind",
                    "path": f"{base_path}.kind",
                    "message": f"Unknown provider kind '{kind}' — no vendored config schema (known kinds: {known}).",
                }
            )
            continue

        config = entry.get("config")
        provider_schema = envelope["schema"]
        config_validator = jsonschema.Draft202012Validator(provider_schema)
        for err in sorted(config_validator.iter_errors(config), key=lambda e: list(e.path)):
            errors.append(
                {
                    "source": "provider-schema",
                    "code": "provider.schema",
                    "path": f"{base_path}.config" + _json_path_from_iter(list(err.path))[1:],
                    "message": err.message,
                }
            )
        for violation in provider_schemas.unique_violations(provider_schema, config):
            errors.append(
                {
                    "source": "provider-schema",
                    "code": "provider.unique",
                    "path": f"{base_path}.config" + violation["path"][1:],
                    "message": violation["message"],
                }
            )
    return errors


def project_dir(name: str) -> pathlib.Path:
    return SYSTEMS_ROOT / name


def system_json_path(name: str) -> pathlib.Path:
    return project_dir(name) / "system.json"


def runtime_yaml_path(name: str) -> pathlib.Path:
    return project_dir(name) / "anolis-runtime.yaml"


def provider_yaml_path(name: str, provider_id: str) -> pathlib.Path:
    return project_dir(name) / "providers" / f"{provider_id}.yaml"


def running_json_path(name: str) -> pathlib.Path:
    return project_dir(name) / "running.json"


def log_path(name: str) -> pathlib.Path:
    return project_dir(name) / "logs" / "latest.log"


# ---------------------------------------------------------------------------
# PID existence check (cross-platform, no required external deps)
# ---------------------------------------------------------------------------


def _pid_exists(pid: int) -> bool:
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_INFORMATION = 0x0400
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)  # type: ignore[attr-defined]
        if handle == 0:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    # Unix
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def is_running(name: str) -> bool:
    rj = running_json_path(name)
    if not rj.exists():
        return False
    try:
        data = json.loads(rj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    pid = data.get("pid")
    if pid is None:
        return False
    return _pid_exists(pid)


def cleanup_stale_running_files() -> None:
    if not SYSTEMS_ROOT.exists():
        return
    for project in SYSTEMS_ROOT.iterdir():
        if not project.is_dir():
            continue
        rj = project / "running.json"
        if rj.exists() and not is_running(project.name):
            print(f"[projects] Cleaning stale running.json for '{project.name}'")
            rj.unlink()


# ---------------------------------------------------------------------------
# Core CRUD
# ---------------------------------------------------------------------------


def sidecar_path(name: str) -> pathlib.Path:
    return project_dir(name) / SIDECAR_NAME


def _read_sidecar(pdir: pathlib.Path) -> dict | None:
    return machine_profile.read_sidecar(pdir)


def _dir_format(pdir: pathlib.Path) -> str | None:
    """Project format for a workspace dir, or None if it isn't a project."""
    sidecar = _read_sidecar(pdir)
    if sidecar and sidecar.get("format") == FORMAT_MACHINE_PROFILE:
        return FORMAT_MACHINE_PROFILE
    if (pdir / "system.json").is_file():
        return FORMAT_SYSTEM
    if (pdir / machine_profile.PROFILE_FILENAME).is_file():
        return FORMAT_MACHINE_PROFILE
    return None


def project_format(name: str) -> str:
    fmt = _dir_format(project_dir(name))
    if fmt is None:
        raise FileNotFoundError(f"Project '{name}' not found")
    return fmt


def list_projects() -> list:
    if not SYSTEMS_ROOT.exists():
        return []
    result = []
    for d in sorted(SYSTEMS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        fmt = _dir_format(d)
        if fmt == FORMAT_SYSTEM:
            try:
                data = json.loads((d / "system.json").read_text(encoding="utf-8"))
                result.append({"name": d.name, "format": fmt, "meta": data.get("meta", {})})
            except (json.JSONDecodeError, OSError):
                pass
        elif fmt == FORMAT_MACHINE_PROFILE:
            sidecar = _read_sidecar(d) or {}
            meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
            result.append({"name": d.name, "format": fmt, "meta": meta})
    return result


def _get_imported_project(name: str) -> dict:
    pdir = project_dir(name)
    sidecar = _read_sidecar(pdir) or {}
    try:
        profile = machine_profile.load_profile(pdir)
    except machine_profile.ProfileError as exc:
        profile = {}
        warnings = [str(exc)]
    else:
        raw_warnings = sidecar.get("warnings")
        warnings = raw_warnings if isinstance(raw_warnings, list) else []
    return {
        "format": FORMAT_MACHINE_PROFILE,
        "meta": sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {"name": name},
        "profile": profile,
        "warnings": warnings,
    }


def get_project(name: str) -> dict:
    # Imported projects are carried verbatim: parsed for display only, never
    # migrated, never written back.
    if _dir_format(project_dir(name)) == FORMAT_MACHINE_PROFILE:
        return _get_imported_project(name)
    path = system_json_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Project '{name}' not found")
    raw = path.read_text(encoding="utf-8")
    system = json.loads(raw)
    system, migrated = migrations.migrate_system(system)
    if migrated:
        try:
            backup = path.with_suffix(".json.v1.bak")
            if not backup.exists():  # keep the FIRST pre-migration document
                backup.write_text(raw, encoding="utf-8")
            path.write_text(json.dumps(system, indent=2), encoding="utf-8")
        except OSError:
            pass  # read-only workspace: serve the migrated doc, persist next save
    return system


def import_project(source_path: str, name: str) -> tuple[dict, list[str]]:
    """Import a canonical machine-profile directory as a verbatim project (#226).

    Copies machine-profile.yaml + config/ + behaviors/ byte-for-byte and writes
    the workbench sidecar. Returns (project meta, import warnings).
    """
    if project_dir(name).exists():
        raise ValueError(f"Project '{name}' already exists")
    source = pathlib.Path(source_path).expanduser().resolve()
    profile_dir_name = source.name
    if profile_dir_name == "":  # e.g. "/" — deploy needs a real project dir name
        raise ImportValidationError([f"Cannot import {source}: not a project directory"])

    report = machine_profile.validate_project_dir(source, profile_dir_name)
    if not report.ok:
        raise ImportValidationError(report.errors)

    pdir = project_dir(name)
    # Claim the directory FIRST (exist_ok=False): a concurrent same-name import
    # must lose here, before the cleanup branch below can delete a live project.
    try:
        pdir.mkdir(parents=True)
    except FileExistsError as exc:
        raise ValueError(f"Project '{name}' already exists") from exc
    try:
        for entry in machine_profile.copy_entries(report.profile or {}):
            src = source / entry
            if src.is_file():
                shutil.copy2(src, pdir / entry)
            elif src.is_dir():
                shutil.copytree(src, pdir / entry, copy_function=shutil.copy2)
        meta = {
            "name": name,
            "created": datetime.now(timezone.utc).isoformat(),
            "imported_from": str(source),
            "profile_dir_name": profile_dir_name,
        }
        sidecar = {
            "schema_version": 1,
            "format": FORMAT_MACHINE_PROFILE,
            "meta": meta,
            "warnings": report.warnings,
        }
        sidecar_path(name).write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(pdir, ignore_errors=True)
        raise
    return meta, report.warnings


def save_project(name: str, system: dict) -> None:
    if _dir_format(project_dir(name)) == FORMAT_MACHINE_PROFILE:
        raise ReadOnlyProjectError(
            f"Project '{name}' was imported from a machine profile and is carried "
            "verbatim — edit it in its source repository and re-import."
        )
    validation_errors = validate_system_payload(system)
    if validation_errors:
        raise ProjectValidationError(validation_errors)

    pdir = project_dir(name)
    pdir.mkdir(parents=True, exist_ok=True)
    system_json_path(name).write_text(json.dumps(system, indent=2), encoding="utf-8")
    outputs = renderer.render(system, name, systems_dir_name=SYSTEMS_ROOT.name)
    for rel, content in outputs.items():
        out_path = pdir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")


def create_project_from_template(name: str, template: str) -> dict:
    if project_dir(name).exists():
        raise ValueError(f"Project '{name}' already exists")
    tpl_path = TEMPLATES_ROOT / template / "system.json"
    if not tpl_path.exists():
        raise FileNotFoundError(f"Template '{template}' not found")
    system: dict = json.loads(tpl_path.read_text(encoding="utf-8"))
    system["meta"]["name"] = name
    system["meta"]["created"] = datetime.now(timezone.utc).isoformat()
    system["meta"]["template"] = template
    save_project(name, system)
    return system


def rename_project(old_name: str, new_name: str) -> None:
    if not project_dir(old_name).exists():
        raise FileNotFoundError(f"Project '{old_name}' not found")
    if is_running(old_name):
        raise ValueError(f"Project '{old_name}' is running")
    if project_dir(new_name).exists():
        raise ValueError(f"Project '{new_name}' already exists")
    project_dir(old_name).rename(project_dir(new_name))


def duplicate_project(source_name: str, new_name: str) -> dict:
    src = project_dir(source_name)
    if not src.exists():
        raise FileNotFoundError(f"Project '{source_name}' not found")
    if project_dir(new_name).exists():
        raise ValueError(f"Project '{new_name}' already exists")
    shutil.copytree(
        src,
        project_dir(new_name),
        ignore=shutil.ignore_patterns("running.json", "logs"),
    )
    try:
        if _dir_format(project_dir(new_name)) == FORMAT_MACHINE_PROFILE:
            # Verbatim copy — only the sidecar meta is workbench-owned.
            sidecar = _read_sidecar(project_dir(new_name)) or {
                "schema_version": 1,
                "format": FORMAT_MACHINE_PROFILE,
                "warnings": [],
            }
            raw_meta = sidecar.get("meta")
            meta = raw_meta if isinstance(raw_meta, dict) else {}
            meta["name"] = new_name
            meta["created"] = datetime.now(timezone.utc).isoformat()
            sidecar["meta"] = meta
            sidecar_path(new_name).write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
            return get_project(new_name)
        system = get_project(new_name)
        system["meta"]["name"] = new_name
        system["meta"]["created"] = datetime.now(timezone.utc).isoformat()
        save_project(new_name, system)
    except Exception:
        # Don't leave a half-initialized copy behind (e.g. the source fails
        # validation post-migration) — the source project is untouched.
        shutil.rmtree(project_dir(new_name), ignore_errors=True)
        raise
    return system


def delete_project(name: str) -> None:
    if not project_dir(name).exists():
        raise FileNotFoundError(f"Project '{name}' not found")
    if is_running(name):
        raise ValueError(f"Project '{name}' is running")
    shutil.rmtree(project_dir(name))
