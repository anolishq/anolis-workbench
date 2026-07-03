"""Provision-track HTTP handlers for the Workbench server.

Exposes endpoints for triggering and monitoring provisioning jobs from the
Tauri desktop UI. Jobs run in background threads; progress is streamed via SSE.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anolis_workbench.core import deploy, installer
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core.executor import ParamikoSSHExecutor
from anolis_workbench.core.paths import DEFAULT_INSTALL_PREFIX

# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------


@dataclass
class ProvisionJob:
    """In-progress or completed provision job."""

    job_id: str
    status: str = "running"  # running | done | failed | cancelled
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    _cancel: bool = field(default=False, repr=False)


_jobs: dict[str, ProvisionJob] = {}
_jobs_lock = threading.Lock()


def _get_job(job_id: str) -> ProvisionJob | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _create_job() -> ProvisionJob:
    job = ProvisionJob(job_id=uuid.uuid4().hex[:12])
    with _jobs_lock:
        _jobs[job.job_id] = job
    return job


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _prepare_workspace(params: dict[str, Any], progress: Any) -> tuple[dict[str, Any], Path]:
    """Authoring: ensure the local workspace project exists; return (system, project_dir)."""
    project = params.get("project", "bioreactor-v1")
    template = params.get("template", "bioreactor-manual")
    prefix = Path(params.get("install_prefix", str(DEFAULT_INSTALL_PREFIX)))

    project_dir = paths_module.SYSTEMS_ROOT / project
    if not project_dir.exists() or params.get("force", False):
        progress("project", f"Creating workspace project {project} from {template}")
        installer.provision_project(template, project, prefix, force=params.get("force", False))
    system = json.loads((project_dir / "system.json").read_text(encoding="utf-8"))
    return system, project_dir


def _run_install_job(job: ProvisionJob, params: dict[str, Any]) -> None:
    """Run install in a background thread, pushing events to the job."""

    def _progress(step: str, detail: str = "") -> None:
        event = {"stage": step, "detail": detail}
        job.events.append(event)

    try:
        system, project_dir = _prepare_workspace(params, _progress)
        result = deploy.deploy_local(
            system=system,
            project_name=params.get("project", "bioreactor-v1"),
            workspace_dir=project_dir,
            prefix=Path(params.get("install_prefix", str(DEFAULT_INSTALL_PREFIX))),
            progress_callback=_progress,
        )
        job.events.append(
            {
                "stage": "done",
                "summary": {"runtime_version": result.runtime_version},
            }
        )
        job.status = "done"
    except Exception as exc:
        job.error = str(exc)
        job.status = "failed"
        job.events.append({"stage": "error", "detail": str(exc)})


def _run_remote_job(job: ProvisionJob, params: dict[str, Any]) -> None:
    """Run remote provision in a background thread using ParamikoSSHExecutor."""

    def _progress(step: str, detail: str = "") -> None:
        event = {"stage": step, "detail": detail}
        job.events.append(event)

    executor = None
    try:
        target = params["target"]
        user, host = target.split("@", 1)
        executor = ParamikoSSHExecutor(
            host=host,
            user=user,
            key_file=params.get("key_file"),
            port=params.get("port", 22),
        )

        # Authoring stays local; the target only receives the deployment.
        system, project_dir = _prepare_workspace(params, _progress)
        result = deploy.deploy_remote(
            executor=executor,
            system=system,
            project_name=params.get("project", "bioreactor-v1"),
            workspace_dir=project_dir,
            prefix=Path(params.get("install_prefix", str(DEFAULT_INSTALL_PREFIX))),
            progress_callback=_progress,
        )
        # Auto-add host to fleet registry
        from anolis_workbench.core.fleet import auto_register_host

        auto_register_host(
            host=host,
            project=params.get("project", "bioreactor-v1"),
            template=params.get("template", "bioreactor-manual"),
        )
        job.events.append(
            {
                "stage": "done",
                "summary": {"runtime_version": result.runtime_version},
            }
        )
        job.status = "done"
    except Exception as exc:
        job.error = str(exc)
        job.status = "failed"
        job.events.append({"stage": "error", "detail": str(exc)})
    finally:
        if executor is not None:
            executor.close()


# ---------------------------------------------------------------------------
# HTTP Handlers
# ---------------------------------------------------------------------------


def start_install(handler: Any) -> None:
    """POST /api/provision/install — start a local install job."""
    content_length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(content_length) if content_length else b"{}"
    params = json.loads(body)

    job = _create_job()
    thread = threading.Thread(target=_run_install_job, args=(job, params), daemon=True)
    thread.start()

    handler._json(202, {"job_id": job.job_id})


def start_remote(handler: Any) -> None:
    """POST /api/provision/remote — start a remote provision job."""
    content_length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(content_length) if content_length else b"{}"
    params = json.loads(body)

    if "target" not in params:
        handler._json(400, {"error": "Missing 'target' field (user@host)"})
        return

    job = _create_job()
    thread = threading.Thread(target=_run_remote_job, args=(job, params), daemon=True)
    thread.start()

    handler._json(202, {"job_id": job.job_id})


def get_status(handler: Any, job_id: str) -> None:
    """GET /api/provision/status/<job_id> — SSE stream of job progress."""
    job = _get_job(job_id)
    if job is None:
        handler._json(404, {"error": f"Job {job_id} not found"})
        return

    # Send as SSE (Server-Sent Events)
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    sent = 0
    import time

    while True:
        # Send any new events
        while sent < len(job.events):
            event = job.events[sent]
            data = json.dumps(event)
            handler.wfile.write(f"data: {data}\n\n".encode())
            handler.wfile.flush()
            sent += 1

        if job.status in ("done", "failed", "cancelled"):
            # Send final status event
            final = json.dumps({"stage": "status", "status": job.status, "error": job.error})
            handler.wfile.write(f"data: {final}\n\n".encode())
            handler.wfile.flush()
            break

        time.sleep(0.2)


def cancel_job(handler: Any, job_id: str) -> None:
    """POST /api/provision/cancel/<job_id> — cancel a running job."""
    job = _get_job(job_id)
    if job is None:
        handler._json(404, {"error": f"Job {job_id} not found"})
        return

    if job.status == "running":
        job._cancel = True
        job.status = "cancelled"
        handler._json(200, {"job_id": job_id, "status": "cancelled"})
    else:
        handler._json(409, {"error": f"Job already {job.status}"})


# ---------------------------------------------------------------------------
# Bundle export
# ---------------------------------------------------------------------------

_bundle_artifacts: dict[str, Path] = {}
_bundle_artifacts_lock = threading.Lock()


def _run_bundle_job(job: ProvisionJob, params: dict[str, Any]) -> None:
    """Run bundle creation in a background thread via install.sh --stage."""
    import platform
    import tempfile

    def _progress(step: str, detail: str = "") -> None:
        job.events.append({"stage": step, "detail": detail})

    try:
        arch = params.get("arch") or ("arm64" if platform.machine() in ("aarch64", "arm64") else "x86_64")
        if arch == "aarch64":
            arch = "arm64"
        project = params.get("project", "bioreactor-v1")
        template = params.get("template", "bioreactor-manual")

        _progress("resolve", f"Building bundle for {project} ({arch})")

        tmp_dir = Path(tempfile.mkdtemp(prefix="anolis-bundle-"))
        tpl_dir = paths_module.TEMPLATES_ROOT / template
        tpl_path = tpl_dir / "system.json"
        if not tpl_path.exists():
            raise RuntimeError(f"Template '{template}' not found at {tpl_path}")
        system = json.loads(tpl_path.read_text(encoding="utf-8"))

        tarball_path = deploy.stage_bundle(
            system=system,
            project_name=project,
            workspace_dir=tpl_dir,
            out_dir=tmp_dir,
            arch=arch,
            progress_callback=_progress,
        )

        with _bundle_artifacts_lock:
            _bundle_artifacts[job.job_id] = tarball_path

        job.events.append(
            {
                "stage": "done",
                "detail": f"Bundle ready: {tarball_path.name}",
                "filename": tarball_path.name,
            }
        )
        job.status = "done"
    except Exception as exc:
        job.error = str(exc)
        job.status = "failed"
        job.events.append({"stage": "error", "detail": str(exc)})


def start_bundle(handler: Any) -> None:
    """POST /api/provision/bundle — start a bundle creation job."""
    content_length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(content_length) if content_length else b"{}"
    params = json.loads(body)

    job = _create_job()
    thread = threading.Thread(target=_run_bundle_job, args=(job, params), daemon=True)
    thread.start()

    handler._json(202, {"job_id": job.job_id})


def download_bundle(handler: Any, job_id: str) -> None:
    """GET /api/provision/bundle/<job_id> — download completed bundle tarball."""
    job = _get_job(job_id)
    if job is None:
        handler._json(404, {"error": f"Job {job_id} not found"})
        return

    if job.status != "done":
        handler._json(409, {"error": f"Job not complete (status: {job.status})"})
        return

    with _bundle_artifacts_lock:
        tarball_path = _bundle_artifacts.get(job_id)

    if tarball_path is None or not tarball_path.exists():
        handler._json(404, {"error": "Bundle artifact not found"})
        return

    data = tarball_path.read_bytes()
    filename = tarball_path.name

    handler.send_response(200)
    handler.send_header("Content-Type", "application/gzip")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.end_headers()
    handler.wfile.write(data)
