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

from anolis_workbench.core import installer
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


def _run_install_job(job: ProvisionJob, params: dict[str, Any]) -> None:
    """Run install in a background thread, pushing events to the job."""

    def _progress(step: str, detail: str = "") -> None:
        event = {"stage": step, "detail": detail}
        job.events.append(event)

    try:
        result = installer.install(
            project_name=params.get("project", "bioreactor-v1"),
            template_name=params.get("template", "bioreactor-manual"),
            install_prefix=installer.Path(params.get("install_prefix", str(DEFAULT_INSTALL_PREFIX))),
            github_token=params.get("github_token"),
            force=params.get("force", False),
            skip_preflight=params.get("skip_preflight", False),
            progress_callback=_progress,
        )
        job.events.append(
            {
                "stage": "done",
                "summary": {"versions": result.verified_versions},
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

        # Determine remote systems root
        home_result = executor.run(["sh", "-c", "echo $HOME"])
        remote_home = home_result.stdout.strip() or f"/home/{user}"
        systems_root = installer.Path(f"{remote_home}/.anolis/systems")

        result = installer.install(
            project_name=params.get("project", "bioreactor-v1"),
            template_name=params.get("template", "bioreactor-manual"),
            install_prefix=installer.Path(params.get("install_prefix", str(DEFAULT_INSTALL_PREFIX))),
            github_token=params.get("github_token"),
            force=params.get("force", False),
            skip_preflight=params.get("skip_preflight", False),
            progress_callback=_progress,
            executor=executor,
            systems_root=systems_root,
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
                "summary": {"versions": result.verified_versions},
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
    """Run bundle creation in a background thread."""
    import os
    import platform
    import tarfile
    import tempfile

    import requests

    def _progress(step: str, detail: str = "") -> None:
        job.events.append({"stage": step, "detail": detail})

    try:
        arch = params.get("arch") or ("arm64" if platform.machine() in ("aarch64", "arm64") else "x86_64")
        project = params.get("project", "bioreactor-v1")
        template = params.get("template", "bioreactor-manual")

        _progress("resolve", f"Building bundle for {project} ({arch})")

        token = params.get("github_token") or os.environ.get("GITHUB_TOKEN")
        tmp_dir = Path(tempfile.mkdtemp(prefix="anolis-bundle-"))

        from anolis_workbench.core import bundler

        arch_map = {
            "arm64": "linux-arm64",
            "aarch64": "linux-arm64",
            "x86_64": "linux-x86_64",
        }
        platform_str = arch_map.get(arch, f"linux-{arch}")
        matrix = installer.load_compat_matrix(None)

        # Resolve components
        _progress("resolve", "Resolving components")
        components = installer.resolve_components(matrix)
        if not components:
            raise RuntimeError("No components found in compatibility matrix")

        # Download tarballs
        session = requests.Session()
        tarballs: list[tuple[installer.ComponentSpec, bytes]] = []

        for comp in components:
            _progress("manifest", f"Fetching manifest for {comp.name} v{comp.version}")
            manifest = installer.fetch_manifest(session, comp.repo, comp.version, platform_str, token=token)
            _progress("download", f"Downloading {manifest.asset_name}")
            data = installer.download_and_verify(session, manifest.download_url, manifest.sha256, token=token)
            tarballs.append((comp, data))

        # Build bundle
        _progress("build", "Assembling bundle")
        out_dir = tmp_dir / f"anolis-{project}-bundle"
        workbench_version = matrix.get("workbench_version", "")
        result = bundler.build_bundle(
            components=components,
            tarballs=tarballs,
            template_name=template,
            project_name=project,
            platform_str=platform_str,
            out_dir=out_dir,
            workbench_version=workbench_version,
        )

        # Create tarball archive
        tarball_path = tmp_dir / f"anolis-{project}-{arch}.tar.gz"
        with tarfile.open(str(tarball_path), "w:gz") as tar:
            tar.add(str(result.bundle_path), arcname=result.bundle_path.name)

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
