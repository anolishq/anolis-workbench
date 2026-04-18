"""Unified Workbench HTTP server implementing Compose, Commission, and Operate tracks."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module
from anolis_workbench.server.routes import commission
from anolis_workbench.server.routes import compose
from anolis_workbench.server.routes import operate


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"WARNING: Invalid integer for {name}: {raw!r}; using {default}", file=sys.stderr)
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    print(f"WARNING: Invalid boolean for {name}: {raw!r}; using {default}", file=sys.stderr)
    return default


HOST = os.getenv("ANOLIS_WORKBENCH_HOST", "127.0.0.1")
PORT = _env_int("ANOLIS_WORKBENCH_PORT", 3010)
OPERATOR_UI_BASE = os.getenv("ANOLIS_OPERATOR_UI_BASE", "http://localhost:3000").rstrip("/")
OPEN_BROWSER = _env_bool("ANOLIS_WORKBENCH_OPEN_BROWSER", True)
FRONTEND_DIR = paths_module.FRONTEND_DIR

_WORKSPACE_ROUTE_RE = re.compile(r"^/projects/[^/]+(?:/(?:compose|commission|operate))?/?$")

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def verify_environment() -> None:
    if not FRONTEND_DIR.is_dir():
        print("ERROR: Workbench frontend directory not found.", file=sys.stderr)
        print(f"  Expected: {FRONTEND_DIR}", file=sys.stderr)
        sys.exit(1)
    if not paths_module.CATALOG_PATH.is_file():
        print("ERROR: Workbench provider catalog not found.", file=sys.stderr)
        print(f"  Expected: {paths_module.CATALOG_PATH}", file=sys.stderr)
        sys.exit(1)


def _open_browser(url: str) -> None:
    time.sleep(0.3)
    webbrowser.open(url)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress noisy per-request logs
        pass

    # ------------------------------------------------------------------
    # HTTP verb dispatch
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/projects":
            compose.list_projects(self)
        elif path.startswith("/api/projects/"):
            name, sub = self._parse_project_path(path)
            if sub is None:
                compose.get_project(self, name)
            elif sub == "logs":
                commission.log_stream(self, name)
            else:
                self._not_found()
        elif path == "/api/status":
            commission.status(self, host=HOST, port=PORT, operator_ui_base=OPERATOR_UI_BASE)
        elif path == "/api/catalog":
            compose.serve_catalog(self)
        elif path == "/api/templates":
            compose.serve_templates(self)
        elif path.startswith("/v0/"):
            operate.proxy_runtime(self, "GET", self.path)
        else:
            self._serve_static(path)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/projects":
            compose.create_project(self)
        elif path.startswith("/api/projects/"):
            name, sub = self._parse_project_path(path)
            if sub == "rename":
                compose.rename_project(self, name)
            elif sub == "duplicate":
                compose.duplicate_project(self, name)
            elif sub == "preflight":
                commission.preflight(self, name)
            elif sub == "launch":
                commission.launch_project(self, name)
            elif sub == "stop":
                commission.stop_project(self, name)
            elif sub == "restart":
                commission.restart_project(self, name)
            elif sub == "export":
                commission.export_project(self, name)
            else:
                self._not_found()
        elif path.startswith("/v0/"):
            operate.proxy_runtime(self, "POST", self.path)
        else:
            self._not_found()

    def do_PUT(self) -> None:
        path = self.path.split("?")[0]
        if path.startswith("/api/projects/"):
            name, sub = self._parse_project_path(path)
            if sub is None:
                compose.save_project(self, name)
            else:
                self._not_found()
        elif path.startswith("/v0/"):
            operate.proxy_runtime(self, "PUT", self.path)
        else:
            self._not_found()

    def do_DELETE(self) -> None:
        path = self.path.split("?")[0]
        if path.startswith("/api/projects/"):
            name, sub = self._parse_project_path(path)
            if sub is None:
                compose.delete_project(self, name)
            else:
                self._not_found()
        elif path.startswith("/v0/"):
            operate.proxy_runtime(self, "DELETE", self.path)
        else:
            self._not_found()

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        decoded = urllib.parse.unquote(path)
        if decoded == "/" or _WORKSPACE_ROUTE_RE.match(decoded):
            self._serve_index()
            return

        rel = decoded.lstrip("/")
        if ".." in rel:
            self._json(400, {"error": "Bad request"})
            return

        file_path = FRONTEND_DIR / rel
        if not file_path.is_file():
            self._json(404, {"error": "Not found"})
            return

        content_type = _MIME.get(file_path.suffix.lower(), "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_index(self) -> None:
        index_path = FRONTEND_DIR / "index.html"
        data = index_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_project_path(path: str):
        """Return (name, sub) from /api/projects/<name>[/<sub>], sub may be None."""
        tail = path[len("/api/projects/") :]
        parts = tail.split("/", 1)
        name = parts[0]
        sub = parts[1] if len(parts) > 1 else None
        return name, sub

    def _body_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._json(400, {"error": "Empty body"})
            return None
        if length > 1_048_576:  # 1 MiB max
            self._json(400, {"error": "Request body too large"})
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self._json(400, {"error": "Invalid JSON"})
            return None

    def _json(self, status: int, data) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self) -> None:
        self._json(404, {"error": "Not found"})


def main() -> None:
    verify_environment()
    projects_module.cleanup_stale_running_files()
    server = ThreadingHTTPServer((HOST, PORT), _Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Anolis Workbench is running at {url}")
    print("Close this window to stop.")
    if OPEN_BROWSER:
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
