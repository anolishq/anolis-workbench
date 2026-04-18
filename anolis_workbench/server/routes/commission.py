"""Commission-track HTTP handlers for the Workbench server."""

from __future__ import annotations

import pathlib
import tempfile

from anolis_workbench.core import exporter as exporter_module
from anolis_workbench.core import launcher as launcher_module
from anolis_workbench.core import projects as projects_module


def status(handler, *, host: str, port: int, operator_ui_base: str) -> None:
    payload = launcher_module.get_status()
    payload["composer"] = {
        "host": host,
        "port": port,
        "operator_ui_base": operator_ui_base,
    }
    payload["workbench"] = {
        "version": 1,
    }
    handler._json(200, payload)


def preflight(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    try:
        system = projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    project_dir = projects_module.project_dir(name)
    result = launcher_module.preflight(name, system, project_dir)
    handler._json(200, result)


def launch_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    try:
        system = projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    project_dir = projects_module.project_dir(name)
    try:
        launcher_module.launch(name, system, project_dir)
        handler._json(200, {"ok": True})
    except RuntimeError as exc:
        handler._json(409, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def stop_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    try:
        projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    launcher_module.stop()
    handler._json(200, {"ok": True})


def restart_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    try:
        projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    project_dir = projects_module.project_dir(name)
    try:
        launcher_module.restart(name, project_dir)
        handler._json(200, {"ok": True})
    except RuntimeError as exc:
        handler._json(409, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def export_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    try:
        projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return

    project_dir = projects_module.project_dir(name)
    filename = f"{name}.anpkg"
    try:
        with tempfile.TemporaryDirectory(prefix="anolis-workbench-export-") as tmp_dir:
            out_path = pathlib.Path(tmp_dir) / filename
            exporter_module.build_package(project_dir=project_dir, out_path=out_path)
            payload = out_path.read_bytes()

        handler.send_response(200)
        handler.send_header("Content-Type", "application/zip")
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
    except exporter_module.ExportError as exc:
        handler._json(400, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def log_stream(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    try:
        projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    launcher_module.handle_log_stream(handler, name)
