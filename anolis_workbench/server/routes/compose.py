"""Compose-track HTTP handlers for the Workbench server."""

from __future__ import annotations

import json

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


def serve_catalog(handler) -> None:
    path = paths_module.CATALOG_PATH
    if not path.exists():
        handler._json(404, {"error": "Catalog not found"})
        return
    handler._json(200, json.loads(path.read_text(encoding="utf-8")))


def serve_templates(handler) -> None:
    tpl_root = paths_module.TEMPLATES_ROOT
    if not tpl_root.exists():
        handler._json(200, [])
        return
    result = []
    for directory in sorted(tpl_root.iterdir()):
        if not directory.is_dir():
            continue
        sj = directory / "system.json"
        if not sj.exists():
            continue
        try:
            data = json.loads(sj.read_text(encoding="utf-8"))
            result.append({"id": directory.name, "meta": data.get("meta", {})})
        except (json.JSONDecodeError, OSError):
            pass
    handler._json(200, result)
