"""Operate-track proxy handlers for the Workbench server."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from anolis_workbench.core import launcher as launcher_module
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module
from anolis_workbench.core import workbench_service

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


def runtime_url_override() -> str:
    """The remote runtime the proxy targets instead of the launched one, or ""."""
    return os.environ.get("ANOLIS_WORKBENCH_RUNTIME_URL", "").strip()


# One DNS label; a bind is an IP address or dot-separated labels, nothing else.
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")


def _bind_host(bind: str) -> str | None:
    """A project's http.bind as the host part of a URL, or None if it is not
    a bare address. Userinfo, a path, a fragment or a port would let the
    project point the proxy somewhere the bind does not name."""
    bracketed = bind.startswith("[") and bind.endswith("]")
    try:
        address = ipaddress.ip_address(bind[1:-1] if bracketed else bind)
    except ValueError:
        return bind if not bracketed and _HOSTNAME.fullmatch(bind) else None
    if address.version == 6:
        return f"[{address}]"
    return None if bracketed else str(address)


def is_local_target(base_url: str) -> bool:
    """True when the URL's host is this machine: loopback, localhost, or the
    unspecified address (0.0.0.0, ::), which connects locally."""
    host = urllib.parse.urlsplit(base_url).hostname
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def runtime_token(target_base: str) -> str:
    """The bearer token the proxy sends to target_base, or "". Never logged.

    ANOLIS_WORKBENCH_RUNTIME_TOKEN wins. Otherwise the local runtime's token,
    from the copy install --workbench-service writes. That one
    goes only to a target on this machine: never to an
    ANOLIS_WORKBENCH_RUNTIME_URL override, and never to a host a project's
    http.bind names.
    """
    explicit = os.environ.get("ANOLIS_WORKBENCH_RUNTIME_TOKEN", "").strip()
    if explicit:
        return explicit
    if runtime_url_override():
        return ""
    if not is_local_target(target_base):
        return ""
    return workbench_service.read_runtime_token()


# The target _runtime_base last resolved. A software stop goes straight to
# it: resolving afresh reads every project, which on a Pi kept the stop
# waiting behind Operate's polls for more than a minute (#612).
_last_target: str | None = None


def _runtime_base() -> tuple[str | None, str | None, int]:
    # Remote override: point the operate proxy at an already-provisioned
    # device instead of the locally-launched runtime. Pairs with
    # ANOLIS_WORKBENCH_RUNTIME_TOKEN for devices that require a Bearer token
    # (auth-by-default installs bind 0.0.0.0 with auth enabled). Interim
    # surface until the device-registry UX (anolishq/anolis#164).
    override = runtime_url_override()
    if override:
        return override.rstrip("/"), None, 200

    status = launcher_module.get_status()
    active_project = status.get("active_project")
    if not status.get("running") or not isinstance(active_project, str) or active_project == "":
        return None, "Runtime is not running", 503

    try:
        system = projects_module.get_project(active_project)
    except FileNotFoundError:
        return None, f"Running project '{active_project}' not found", 500

    global _last_target
    installed = launcher_module.installed_runtime_target(active_project)
    if installed is not None:
        # The installed machine's project: install.sh's copy of the config,
        # not the project's variant, is what the runtime runs (#722).
        _last_target = f"http://{installed[0]}:{installed[1]}"
        return _last_target, None, 200

    # The proxy target comes from the variant that is actually running (#255).
    # Reading a fixed default here would silently point Operate at :8080 while
    # the runtime listens elsewhere — and the launcher WOULD report it running,
    # so the UI shows a live project whose every call 502s.
    runtime_doc = launcher_module.active_runtime_config(system) or {}
    http = runtime_doc.get("http")
    http = http if isinstance(http, dict) else {}
    bind = http.get("bind", "127.0.0.1")
    port = http.get("port", 8080)

    if not isinstance(bind, str) or bind == "":
        bind = "127.0.0.1"
    host = _bind_host(bind)
    if host is None:
        return None, "Runtime bind address is invalid", 500
    if isinstance(port, str):
        try:
            port = int(port)
        except ValueError:
            return None, "Runtime port is invalid", 500
    if not isinstance(port, int) or isinstance(port, bool):
        return None, "Runtime port is invalid", 500

    _last_target = f"http://{host}:{port}"
    return _last_target, None, 200


# The resolved target and token (wb#754): (key, target, token). Resolving
# reads the launcher's status (every project, and a probe of an external
# runtime), the running project and the token file, about 0.2 s on a Pi, and
# Operate sends its polls in parallel, so they queued behind each other. It is
# kept until the key changes, or until the target refuses a request.
_resolved: tuple[tuple, str, str] | None = None


def _stat(path: os.PathLike | str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _resolution_key() -> tuple:
    """What the resolution was read from, cheaply: the overrides, the
    launcher's own process, each project's top-level files (running.json, the
    sidecar, its profile) and config/, each installed prefix's runtime config,
    and the token file. Stats only; no file is read."""
    with launcher_module._state_lock:
        proc = launcher_module._state["process"]
        launched = (proc.pid if proc is not None and proc.poll() is None else None, launcher_module._state["project"])
    files: list[tuple] = []
    for root in sorted({str(paths_module.SYSTEMS_ROOT), str(projects_module.SYSTEMS_ROOT)}):
        try:
            projects = sorted((e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name)
        except OSError:
            continue
        for project in projects:
            for directory in (project.path, os.path.join(project.path, "config")):
                try:
                    entries = sorted((e for e in os.scandir(directory) if e.is_file()), key=lambda e: e.name)
                except OSError:
                    continue
                files.extend((e.path, _stat(e.path)) for e in entries)
    for prefix in _resolved_prefixes:
        path = os.path.join(prefix, "config", "runtime.yaml")
        files.append((path, _stat(path)))
    files.append(("token", _stat(workbench_service.RUNTIME_TOKEN_FILE)))
    overrides = (runtime_url_override(), os.environ.get("ANOLIS_WORKBENCH_RUNTIME_TOKEN", ""))
    return (overrides, launched, tuple(files))


# The installed prefixes the last resolution found, whose runtime configs the
# key watches; read from the sidecars once per resolution, not per request.
_resolved_prefixes: tuple[str, ...] = ()


def _resolve() -> tuple[str | None, str, str | None, int]:
    """(target, token, error, status) for a proxied request, from the cache
    while its key holds."""
    global _resolved, _resolved_prefixes
    key = _resolution_key()
    cached = _resolved
    if cached is not None and cached[0] == key:
        return cached[1], cached[2], None, 200
    base, error, status_code = _runtime_base()
    if base is None:
        _resolved = None
        return None, "", error, status_code
    prefixes: list[str] = []
    try:
        names = sorted(e.name for e in os.scandir(projects_module.SYSTEMS_ROOT) if e.is_dir())
    except OSError:
        names = []
    for name in names:
        prefix = projects_module.installed_from(name)
        if prefix is not None:
            prefixes.append(prefix)
    token = runtime_token(base)
    if tuple(prefixes) != _resolved_prefixes:
        _resolved_prefixes = tuple(prefixes)
        key = _resolution_key()
    _resolved = (key, base, token)
    return base, token, None, 200


def _open(runtime_base: str, token: str, method: str, raw_path: str, body: bytes | None, handler, wants_stream: bool):
    headers: dict[str, str] = {}
    for key in ("Content-Type", "Accept", "Last-Event-ID"):
        value = handler.headers.get(key)
        if value:
            headers[key] = value

    # Bearer auth; never logged. The runtime may require the token on
    # loopback too; runtime_token() hands the copied one only to this machine.
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(f"{runtime_base}{raw_path}", data=body, headers=headers, method=method)
    if wants_stream:
        return urllib.request.urlopen(req)
    return urllib.request.urlopen(req, timeout=15)


def _unauthorized_body(token_sent: bool) -> bytes:
    """The runtime's own error shape, saying what to run (#722). The runtime
    says only "unauthorized", which Operate showed as it stood."""
    if token_sent:
        message = (
            "The runtime refused the workbench's token. Run `anolis-provision workbench-service` "
            "on this machine to refresh the copy after a token rotation, or correct "
            "ANOLIS_WORKBENCH_RUNTIME_TOKEN if it is set."
        )
    else:
        message = (
            "The runtime requires a token and the workbench has no token for it. Run "
            "`anolis-provision workbench-service` on this machine to install one, or set "
            "ANOLIS_WORKBENCH_RUNTIME_TOKEN for a runtime on another machine."
        )
    return json.dumps({"status": {"code": "UNAUTHENTICATED", "message": message}}).encode("utf-8")


def proxy_runtime(handler, method: str, raw_path: str) -> None:
    global _resolved
    is_stop = method == "POST" and raw_path.split("?")[0] == "/v0/estop"
    remembered = _last_target if is_stop and not runtime_url_override() else None
    if remembered is not None:
        runtime_base = remembered
        token = runtime_token(remembered)
    else:
        resolved, token, error, status_code = _resolve()
        if resolved is None:
            handler._json(status_code, {"error": error or "Runtime unavailable"})
            return
        runtime_base = resolved

    body: bytes | None = None
    content_length = int(handler.headers.get("Content-Length", "0") or "0")
    if content_length > 0:
        body = handler.rfile.read(content_length)

    wants_stream = raw_path.split("?")[0] == "/v0/events"

    try:
        try:
            response_ctx = _open(runtime_base, token, method, raw_path, body, handler, wants_stream)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError:
            # Nothing answers at the remembered or cached target: the runtime
            # stopped, or another project's now runs. Resolve it afresh.
            _resolved = None
            resolved, token, error, status_code = _resolve()
            if resolved is None:
                handler._json(status_code, {"error": error or "Runtime unavailable"})
                return
            if resolved == runtime_base:
                raise
            runtime_base = resolved
            response_ctx = _open(resolved, token, method, raw_path, body, handler, wants_stream)

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
                    # read1, not read: read(4096) waits for 4096 bytes, so events
                    # sat in the proxy and a closed tab went unnoticed (#616).
                    chunk = response.read1(4096)
                    if not chunk:
                        break
                    try:
                        handler.wfile.write(chunk)
                        handler.wfile.flush()
                    except OSError:
                        # The client went away (a closed tab); stop reading
                        # the runtime's stream for it.
                        handler.close_connection = True
                        break
                return

            data = response.read()
            handler.wfile.write(data)
    except urllib.error.HTTPError as exc:
        body_data = exc.read()
        if exc.code == 401:
            body_data = _unauthorized_body(bool(token))
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
