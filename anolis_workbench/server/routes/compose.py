"""Compose-track HTTP handlers for the Workbench server."""

from __future__ import annotations

import urllib.parse

from anolis_workbench.core import machine_profile, provider_import, provider_schemas
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module


def list_projects(handler) -> None:
    handler._json(200, projects_module.list_projects())


def create_project(handler) -> None:
    body = handler._body_json()
    if body is None:
        return
    name = body.get("name") or ""
    template = body.get("template") or ""
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    if not template:
        handler._json(400, {"error": "template required"})
        return
    if not isinstance(template, str) or projects_module.validate_name(template):
        handler._json(400, {"error": "template must be 1-64 characters: letters, digits, hyphens, underscores only."})
        return
    try:
        system = projects_module.create_project_from_template(name, template)
        handler._json(201, system)
    except projects_module.ProjectValidationError as exc:
        handler._json(
            500,
            {
                "error": "Template produced invalid system payload",
                "code": "template_validation_failed",
                "errors": exc.errors,
            },
        )
    except FileNotFoundError as exc:
        handler._json(404, {"error": str(exc)})
    except ValueError as exc:
        handler._json(409, {"error": str(exc)})


def import_project(handler) -> None:
    """POST /api/projects/import — import a canonical machine-profile dir (#226).

    The caller-supplied path is canonicalized and allowlist-checked inside
    `projects.import_project`; this handler never builds a filesystem path.
    """
    body = handler._body_json()
    if body is None:
        return
    requested_name = body.get("name")
    if requested_name is not None and not isinstance(requested_name, str):
        handler._json(400, {"error": "name must be a string"})
        return
    try:
        meta, warnings = projects_module.import_project(body.get("path"), requested_name or None)
        name = meta["name"]
        handler._json(
            201,
            {"name": name, "format": projects_module.FORMAT_MACHINE_PROFILE, "meta": meta, "warnings": warnings},
        )
    except machine_profile.ImportSourceError as exc:
        handler._json(400, {"error": str(exc), "code": "import_source_rejected"})
    except projects_module.ImportValidationError as exc:
        handler._json(
            400,
            {
                "error": "Machine-profile directory failed import validation",
                "code": "import_validation_failed",
                "errors": [
                    {"source": "import", "code": "import.validation", "path": "$", "message": m} for m in exc.errors
                ],
            },
        )
    except ValueError as exc:
        handler._json(409, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


# A base64 upload of projects.UPLOAD_MAX_BYTES, with its JSON around it.
UPLOAD_BODY_MAX_BYTES = 4 * 1024 * 1024


def import_uploaded_project(handler) -> None:
    """POST /api/projects/import-upload: import a project folder the browser
    uploaded (#749). Every path and limit is checked in
    `projects.import_uploaded_project` before anything is written."""
    body = handler._body_json(max_bytes=UPLOAD_BODY_MAX_BYTES)
    if body is None:
        return
    requested_name = body.get("name")
    if requested_name is not None and not isinstance(requested_name, str):
        handler._json(400, {"error": "name must be a string"})
        return
    try:
        meta, warnings = projects_module.import_uploaded_project(
            body.get("folder"), body.get("files"), requested_name or None
        )
        handler._json(
            201,
            {
                "name": meta["name"],
                "format": projects_module.FORMAT_MACHINE_PROFILE,
                "meta": meta,
                "warnings": warnings,
            },
        )
    except projects_module.ImportValidationError as exc:
        handler._json(
            400,
            {
                "error": "Machine-profile directory failed import validation",
                "code": "import_validation_failed",
                "errors": [
                    {"source": "import", "code": "import.validation", "path": "$", "message": m} for m in exc.errors
                ],
            },
        )
    except ValueError as exc:
        handler._json(409, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def get_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    try:
        handler._json(200, projects_module.get_project(name))
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})


def save_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    system = handler._body_json()
    if system is None:
        return
    try:
        projects_module.save_project(name, system)
        handler._json(200, {"ok": True})
    except projects_module.ReadOnlyProjectError as exc:
        handler._json(409, {"error": str(exc), "code": "read_only_project"})
    except projects_module.ProjectValidationError as exc:
        handler._json(
            400,
            {
                "error": "Project validation failed",
                "code": "validation_failed",
                "errors": exc.errors,
            },
        )
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def rename_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    body = handler._body_json()
    if body is None:
        return
    new_name = body.get("new_name") or ""
    err = projects_module.validate_name(new_name)
    if err:
        handler._json(400, {"error": err or "new_name required"})
        return
    if projects_module.is_running(name):
        handler._json(409, {"error": "Cannot rename a running project"})
        return
    try:
        projects_module.rename_project(name, new_name)
        handler._json(200, {"ok": True})
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
    except ValueError as exc:
        handler._json(409, {"error": str(exc)})


def duplicate_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    body = handler._body_json()
    if body is None:
        return
    new_name = body.get("new_name") or ""
    err = projects_module.validate_name(new_name)
    if err:
        handler._json(400, {"error": err or "new_name required"})
        return
    try:
        system = projects_module.duplicate_project(name, new_name)
        handler._json(201, system)
    except projects_module.ProjectValidationError as exc:
        handler._json(
            500,
            {
                "error": "Duplicated project failed validation",
                "code": "duplicate_validation_failed",
                "errors": exc.errors,
            },
        )
    except FileNotFoundError as exc:
        handler._json(404, {"error": str(exc)})
    except ValueError as exc:
        handler._json(409, {"error": str(exc)})


def delete_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    if projects_module.is_running(name):
        handler._json(409, {"error": "Cannot delete a running project"})
        return
    try:
        projects_module.delete_project(name)
        handler._json(200, {"ok": True})
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})


def serve_provider_schemas(handler) -> None:
    """Provider config-schema envelopes, passed through verbatim (#270).

    kind -> the vendored `--config-schema` envelope (executable profile v1 §2);
    the composer renders provider config forms from these schemas.
    """
    handler._json(200, {"schema_version": 1, "providers": provider_schemas.all_envelopes()})


def resolve_provider_schemas(handler) -> None:
    """The envelope each pinned provider version resolves to (#784).

    Body: {"pins": {kind: version}}. The reply has every available kind, as
    /api/provider-schemas does, resolved at its pin, plus what resolution
    decided (source, exact, the versions, an import's sha256). The composer
    asks again whenever a pin changes, so its forms match what a save checks.
    """
    body = handler._body_json()
    if body is None:
        return
    pins = body.get("pins") if isinstance(body, dict) else None
    if not isinstance(pins, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in pins.items()):
        handler._json(400, {"error": 'Body must be {"pins": {kind: version}} with string versions'})
        return
    handler._json(200, {"schema_version": 1, **provider_schemas.resolve_pins(pins)})


def import_provider_schema(handler) -> None:
    """Import a provider's --config-schema envelope over HTTP (#787).

    For the desktop app, which has no CLI on PATH. The body is the envelope
    file's bytes, so the recorded sha256 is the file's; `replace`, `repo` and
    `filename` come in the query. The checks and refusals are the CLI's, and
    a running workbench offers the kind at once.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)

    def param(name: str) -> str | None:
        values = query.get(name)
        return values[-1] if values else None

    try:
        length = int(handler.headers.get("Content-Length", 0))
    except ValueError:
        length = 0
    if length <= 0:
        handler._json(400, {"error": "Empty body: send the envelope file's bytes"})
        return
    if length > provider_import.MAX_ENVELOPE_BYTES:
        handler._json(
            400, {"error": f"Request body too large: an envelope is at most {provider_import.MAX_ENVELOPE_BYTES} bytes"}
        )
        return
    raw = handler.rfile.read(length)
    filename = "".join(c for c in (param("filename") or "") if c.isprintable()).strip()[:255]
    try:
        result = provider_import.import_bytes(
            raw,
            origin=f"upload: {filename}" if filename else "upload",
            replace=param("replace") in ("1", "true"),
            repo=param("repo"),
        )
    except provider_import.ImportRefused as exc:
        handler._json(400, {"error": str(exc)})
        return
    handler._json(
        200,
        {
            "kind": result.kind,
            "version": result.version,
            "status": result.status,
            "sha256": result.sha256,
            "repo": result.repo,
        },
    )


def serve_templates(handler) -> None:
    """Bundled canonical project templates (#255)."""
    tpl_root = paths_module.TEMPLATES_ROOT
    if not tpl_root.exists():
        handler._json(200, [])
        return
    result = []
    for directory in sorted(tpl_root.iterdir()):
        if not directory.is_dir():
            continue
        try:
            profile = machine_profile.load_profile(directory)
        except machine_profile.ProfileError as exc:
            # Listed with its error rather than dropped, so a broken template
            # is visible instead of silently missing (#356).
            result.append({"id": directory.name, "meta": {"name": directory.name}, "error": str(exc)})
            continue
        result.append(
            {
                "id": directory.name,
                "meta": {"name": profile.get("display_name") or directory.name},
            }
        )
    handler._json(200, result)
