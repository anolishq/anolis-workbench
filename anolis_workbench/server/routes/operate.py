"""Operate-track proxy handlers for the Workbench server."""

from __future__ import annotations

import urllib.error
import urllib.request

from anolis_workbench.core import launcher as launcher_module
from anolis_workbench.core import projects as projects_module

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _runtime_base() -> tuple[str | None, str | None, int]:
    status = launcher_module.get_status()
    active_project = status.get("active_project")
    if not status.get("running") or not isinstance(active_project, str) or active_project == "":
        return None, "Runtime is not running", 503

    try:
        system = projects_module.get_project(active_project)
    except FileNotFoundError:
        return None, f"Running project '{active_project}' not found", 500

    runtime_cfg = system.get("topology", {}).get("runtime", {})
    bind = runtime_cfg.get("http_bind", "127.0.0.1")
    port = runtime_cfg.get("http_port", 8080)

    if not isinstance(bind, str) or bind == "":
        bind = "127.0.0.1"
    if not isinstance(port, int):
        return None, "Runtime port is invalid", 500

    return f"http://{bind}:{port}", None, 200


def proxy_runtime(handler, method: str, raw_path: str) -> None:
    runtime_base, error, status_code = _runtime_base()
    if runtime_base is None:
        handler._json(status_code, {"error": error or "Runtime unavailable"})
        return

    target_url = f"{runtime_base}{raw_path}"
    body: bytes | None = None
    content_length = int(handler.headers.get("Content-Length", "0") or "0")
    if content_length > 0:
        body = handler.rfile.read(content_length)

    headers: dict[str, str] = {}
    for key in ("Content-Type", "Accept", "Last-Event-ID"):
        value = handler.headers.get(key)
        if value:
            headers[key] = value

    req = urllib.request.Request(target_url, data=body, headers=headers, method=method)
    wants_stream = raw_path.split("?")[0] == "/v0/events"

    try:
        if wants_stream:
            response_ctx = urllib.request.urlopen(req)
        else:
            response_ctx = urllib.request.urlopen(req, timeout=15)

        with response_ctx as response:
            handler.send_response(int(response.status))
            for key, value in response.headers.items():
                lower = key.lower()
                if lower in _HOP_BY_HOP_HEADERS:
                    continue
                if lower == "content-length" and wants_stream:
                    continue
                handler.send_header(key, value)
            handler.end_headers()

            if wants_stream:
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
                return

            data = response.read()
            handler.wfile.write(data)
    except urllib.error.HTTPError as exc:
        body_data = exc.read()
        handler.send_response(int(exc.code))
        for key, value in exc.headers.items():
            lower = key.lower()
            if lower in _HOP_BY_HOP_HEADERS:
                continue
            if lower == "content-length":
                continue
            handler.send_header(key, value)
        handler.send_header("Content-Length", str(len(body_data)))
        handler.end_headers()
        if body_data:
            handler.wfile.write(body_data)
    except urllib.error.URLError as exc:
        handler._json(502, {"error": f"Runtime proxy failed: {exc}"})
