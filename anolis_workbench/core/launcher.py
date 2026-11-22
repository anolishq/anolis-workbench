"""Process management for Anolis Workbench.

Preflight checks, process launch, stop, restart, and SSE log streaming.
All public functions are called from server.py HTTP handlers.
"""

import copy
import json
import os
import pathlib
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import yaml

from anolis_workbench.core import canonical, provider_schemas
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core.child_env import child_env

_state: dict = {
    "project": None,  # active project name
    "process": None,  # subprocess.Popen handle
    "log_lines_by_project": {},  # dict[str, list[str]] ring buffers (last 200 lines)
}
_state_lock = threading.Lock()
# Held by launch(), restart() and stop() from their running check until the
# runtime they start is published, or they fail: the server runs each request
# on its own thread, and two overlapping launches used to both spawn a runtime
# (#671). Reentrant, since restart() calls stop(). Separate from _state_lock,
# which the log reader takes for every line.
_lifecycle_lock = threading.RLock()

_sse_subscribers: dict[str, list] = {}  # dict[project_name, list[queue.Queue]]
_sse_lock = threading.Lock()


def running_json_path(name: str) -> pathlib.Path:
    return paths_module.SYSTEMS_ROOT / name / "running.json"


def _resolve_executable_path(path_value: str | None) -> pathlib.Path | None:
    if not path_value:
        return None
    return paths_module.resolve_data_path(path_value)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_status() -> dict:
    """Return serialisable status dict for GET /api/status."""
    runtime = _current_runtime_snapshot(clean_stale=True)
    return {
        "version": 1,
        "active_project": runtime["project"],
        "running": runtime["running"],
        "pid": runtime["pid"],
        # A runtime the workbench did not launch, such as the systemd service
        # install.sh installs: its own Stop and Restart cannot reach it (wb#581).
        "external": bool(runtime.get("external")),
    }


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass

    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)  # type: ignore[attr-defined]
        if handle == 0:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _terminate_pid(pid: int, timeout_s: float = 5.0) -> None:
    if pid <= 0:
        return

    _psutil: Any = None
    try:
        import psutil as _psutil_import

        _psutil = _psutil_import
    except ImportError:
        pass

    if _psutil is not None:
        try:
            proc = _psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=timeout_s)
            except _psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout_s)
            return
        except _psutil.Error:
            return

    if os.name == "nt":
        import ctypes

        PROCESS_TERMINATE = 0x0001
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)  # type: ignore[attr-defined]
        if handle == 0:
            return
        try:
            ctypes.windll.kernel32.TerminateProcess(handle, 1)  # type: ignore[attr-defined]
            ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))  # type: ignore[attr-defined]
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _pid_exists(pid):
            return
        time.sleep(0.1)
    if _pid_exists(pid):
        try:
            _sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(pid, _sigkill)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _discover_running_runtime(clean_stale: bool = False) -> dict | None:
    systems_dir = paths_module.SYSTEMS_ROOT
    if not systems_dir.exists():
        return None

    for project_dir in sorted(systems_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        running_path = project_dir / "running.json"
        if not running_path.exists():
            continue

        try:
            data = json.loads(running_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            if clean_stale:
                try:
                    running_path.unlink(missing_ok=True)
                except OSError:
                    pass
            continue

        # Valid JSON that is not an object is as stale as unparseable JSON (#674).
        pid = data.get("pid") if isinstance(data, dict) else None
        if not isinstance(pid, int):
            if clean_stale:
                try:
                    running_path.unlink(missing_ok=True)
                except OSError:
                    pass
            continue

        if _pid_exists(pid):
            return {
                "running": True,
                "project": project_dir.name,
                "pid": pid,
                "managed": False,
            }

        if clean_stale:
            try:
                running_path.unlink(missing_ok=True)
            except OSError:
                pass

    return None


def _runtime_alive(bind: str, port: int, timeout_s: float = 0.5) -> bool:
    """True if an anolis-runtime answers /v0/runtime/status on bind:port.

    The probe sends no token. A runtime that requires one on loopback answers
    401, which still proves a runtime is there, so 401 counts as alive. A
    refused connection, a timeout, any other error status, and a 200 without
    a version are not alive.
    """
    import json as _json
    import urllib.error
    import urllib.request

    url = f"http://{bind}:{port}/v0/runtime/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # noqa: S310 — caller restricts bind to loopback
            if resp.status != 200:
                return False
            return "version" in _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code == 401
    except Exception:
        return False


def installed_runtime_target(name: str) -> tuple[str, int] | None:
    """(host, port) of the installed runtime when project `name` is the
    installed machine's project (#722): loopback, on the http.port of the
    runtime config install.sh left in that prefix. None for any other project,
    or when that config cannot be read."""
    from anolis_workbench.core import installed_project
    from anolis_workbench.core import projects as projects_module

    prefix = projects_module.installed_from(name)
    if prefix is None:
        return None
    port = installed_project.installed_runtime_port(pathlib.Path(prefix))
    return None if port is None else ("127.0.0.1", port)


def _discover_external_runtime() -> dict | None:
    """Detect a runtime the workbench did not launch (e.g. one installed as a
    systemd service by install.sh) by probing each known project's configured
    runtime HTTP port.

    This keeps the install and operate paths coherent: a machine that has been
    set up and is running is operable from the workbench regardless of who
    started its runtime. Such a runtime is reported managed=False/external=True,
    so stop()/restart() (which only act on workbench-launched processes) leave
    it untouched — it is owned by systemd, not the workbench.

    The probe runs on every call, so a runtime that stops or starts shows at
    once; only the ports to probe are kept between calls (wb#754).
    """
    for project, bind, port in _external_candidates():
        if _runtime_alive(bind, port):
            return {
                "running": True,
                "project": project,
                "pid": None,
                "managed": False,
                "external": True,
            }
    return None


# The ports _discover_external_runtime probes, with the key they were read
# under (wb#754): (key, prefixes, candidates). Finding them loads every
# project's document and the installed config, which on a Pi made each
# /api/status poll 0.1-0.3 s.
_candidates: tuple[tuple, tuple[str, ...], list[tuple[str, str, int]]] | None = None


def _stat(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _candidates_key(prefixes: tuple[str, ...]) -> tuple:
    """Stats of what the candidates were read from: each project's top-level
    files and config/, and each installed prefix's runtime config."""
    files: list[tuple] = []
    try:
        projects = sorted((e for e in os.scandir(paths_module.SYSTEMS_ROOT) if e.is_dir()), key=lambda e: e.name)
    except OSError:
        projects = []
    for project in projects:
        for directory in (project.path, os.path.join(project.path, "config")):
            try:
                entries = sorted((e for e in os.scandir(directory) if e.is_file()), key=lambda e: e.name)
            except OSError:
                continue
            files.extend((e.path, _stat(e.path)) for e in entries)
    for prefix in prefixes:
        path = os.path.join(prefix, "config", "runtime.yaml")
        files.append((path, _stat(path)))
    return tuple(files)


def _external_candidates() -> list[tuple[str, str, int]]:
    """(project, bind, port) for each project whose runtime would be on
    loopback, in project order: the installed project where install.sh's
    health probe looks (#722), any other at its active variant's http port."""
    global _candidates
    cached = _candidates
    if cached is not None and cached[0] == _candidates_key(cached[1]):
        return cached[2]

    systems_dir = paths_module.SYSTEMS_ROOT
    if not systems_dir.exists():
        _candidates = None
        return []

    from anolis_workbench.core import projects as projects_module

    candidates: list[tuple[str, str, int]] = []
    prefixes: list[str] = []
    for project_dir in sorted(systems_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        prefix = projects_module.installed_from(project_dir.name)
        if prefix is not None:
            prefixes.append(prefix)
        installed = installed_runtime_target(project_dir.name)
        if installed is not None:
            candidates.append((project_dir.name, *installed))
            continue
        try:
            document = projects_module.get_project(project_dir.name)
            runtime_doc = active_runtime_config(document)
        except (FileNotFoundError, ValueError, OSError, AttributeError, TypeError):
            continue
        if not isinstance(runtime_doc, dict):
            continue
        http = runtime_doc.get("http")
        if not isinstance(http, dict):
            continue
        bind = http.get("bind") or "127.0.0.1"
        if bind in ("0.0.0.0", ""):
            bind = "127.0.0.1"
        # Only auto-detect a loopback-bound runtime. A non-loopback bind is a
        # remote/LAN target, reached explicitly via ANOLIS_WORKBENCH_RUNTIME_URL
        # (operate.py) — not something to probe automatically on every status poll.
        if bind not in ("127.0.0.1", "localhost", "::1"):
            continue
        port = http.get("port")
        if not isinstance(port, int):
            continue
        candidates.append((project_dir.name, bind, port))
    _candidates = (_candidates_key(tuple(prefixes)), tuple(prefixes), candidates)
    return candidates


def _current_runtime_snapshot(clean_stale: bool = False) -> dict:
    with _state_lock:
        proc = _state["process"]
        if proc is not None and proc.poll() is None:
            return {
                "running": True,
                "project": _state["project"],
                "pid": proc.pid,
                "managed": True,
            }

    discovered = _discover_running_runtime(clean_stale=clean_stale)
    if discovered is not None:
        _follow_detached(discovered["project"], discovered["pid"])
        return discovered

    external = _discover_external_runtime()
    if external is not None:
        return external

    return {"running": False, "project": None, "pid": None, "managed": False}


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight(name: str, system: dict, project_dir: pathlib.Path) -> dict:
    """
    Run preflight checks and return {"ok": bool, "checks": [...], "summary": {...}}
    (see _preflight_result).
    Re-renders YAML to disk before running binary checks.
    """
    from anolis_workbench.core import (
        canonical_validator,  # local import avoids any circular at import time
    )

    checks: list[dict] = []

    # Project the canonical variant to disk first, so --check-config sees the
    # config the runtime will actually be handed.
    try:
        launch_config = materialize_launch_config(project_dir, system)
    except Exception as exc:
        checks.append(
            {
                "name": "Project runtime config for launch",
                "ok": False,
                "error": str(exc),
                "hint": None,
            }
        )
        return _preflight_result(checks)

    # Check 1: Runtime binary
    host_paths = canonical.host_paths({"host_paths": system.get("host_paths")})
    runtime_exe_value = host_paths.get("runtime_executable", "")
    runtime_exe = _resolve_executable_path(runtime_exe_value)
    _exists_check(
        checks,
        "Runtime binary exists",
        runtime_exe,
        hint=_build_binary_hint(runtime_exe, repo="anolis", docs="docs/"),
    )

    # Check 2: Provider binaries
    providers = system.get("providers", {})
    for pid, pcfg in providers.items():
        exe_str = host_paths.get("providers", {}).get(pid, {}).get("executable", "")
        exe = _resolve_executable_path(exe_str)
        kind = pcfg.get("kind", "")
        # A hint string only. The real repo for a kind lives in the machine
        # profile's components.providers pin, which may be any org (#285).
        known_kind = provider_schemas.get_envelope(kind) is not None
        repo = f"anolis-provider-{kind}" if known_kind else None
        docs = "docs/" if known_kind else None
        _exists_check(
            checks,
            f"Provider {pid} binary exists",
            exe,
            hint=_build_binary_hint(exe, repo=repo, docs=docs) if exe is not None else None,
        )

    # Check 3: Output paths writable
    checks.append(_check_writable(project_dir))

    # Check 3b: Runtime port in use
    runtime_doc = active_runtime_config(system) or {}
    rt_port = (runtime_doc.get("http") or {}).get("port")
    if rt_port is not None:
        checks.append(_check_port(rt_port))

    # Check 4: System-level validation
    errors = canonical_validator.validate_project(system)
    if errors:
        for err in errors:
            checks.append({"name": "System-level validation", "ok": False, "error": err, "hint": None})
    else:
        checks.append({"name": "System-level validation", "ok": True, "error": None, "hint": None})

    # Check 5: Runtime --check-config
    checks.append(
        _check_config_binary(
            "Runtime --check-config",
            runtime_exe,
            launch_config,
        )
    )

    # Check 6+: Provider --check-config. The verb is universal per the
    # executable profile (protocol#62); _check_config_binary degrades gracefully
    # ("Not yet available") for a binary that does not recognize the flag.
    for pid in providers:
        exe_str = host_paths.get("providers", {}).get(pid, {}).get("executable", "")
        exe = _resolve_executable_path(exe_str)
        rel = system.get("profile", {}).get("providers", {}).get(pid, {}).get("config", "")
        yaml_path = project_dir / rel if rel else project_dir
        checks.append(
            _check_config_binary(
                f"Provider {pid} --check-config",
                exe,
                yaml_path,
            )
        )
        # The provider says what it needs from this host (executable profile
        # v1 §6); the workbench relays it and knows no transport itself.
        checks.append(_check_host_binary(f"Provider {pid} --check-host", exe, yaml_path))

    return _preflight_result(checks)


def _preflight_result(checks: list[dict]) -> dict:
    """`ok` gates Launch and means "nothing failed". A skipped check (ok None)
    verified nothing, so `summary` counts it apart from the passes."""
    failed = sum(1 for c in checks if c.get("ok") is False)
    passed = sum(1 for c in checks if c.get("ok") is True)
    return {
        "ok": failed == 0,
        "checks": checks,
        "summary": {"passed": passed, "skipped": len(checks) - passed - failed, "failed": failed},
    }


# ---------------------------------------------------------------------------
# Build hints
# ---------------------------------------------------------------------------


def _build_binary_hint(exe: pathlib.Path | None, repo: str | None, docs: str | None) -> str:
    """Return an actionable build hint for a missing binary. It carries no
    per-kind facts: how a provider is built is the provider's to document (#287)."""
    # Detect whether the sibling repo directory exists
    if repo:
        sibling = pathlib.Path("..") / repo
        if sibling.is_dir():
            repo_note = f"Repo '{repo}' found but not built."
        else:
            repo_note = f"Repo not found — clone '{repo}' as a sibling of this repo."
    else:
        repo_note = ""

    parts = []
    if repo_note:
        parts.append(repo_note)
    if docs and repo:
        parts.append(f"Build docs: {repo}/{docs}")
    if not parts:
        parts.append("Set the binary's Executable path in Compose (the project's host paths).")
    return "  ".join(parts)


def _check_port(port: object) -> dict:
    """Check whether a TCP port is already occupied."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port <= 65535:
        # connect_ex raises OverflowError or TypeError on these (#674).
        return {
            "name": f"Runtime port {port} available",
            "ok": False,
            "error": f"Runtime port {port!r} is not a TCP port (1-65535).",
            "hint": "Set http.port in the runtime config to a number from 1 to 65535.",
        }
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        in_use = s.connect_ex(("127.0.0.1", port)) == 0
    if in_use:
        return {
            "name": f"Runtime port {port} available",
            "ok": False,
            "error": f"Port {port} is already in use.",
            "hint": "Stop the existing process or change the runtime port in the config.",
        }
    return {"name": f"Runtime port {port} available", "ok": True, "error": None, "hint": None}


def _exists_check(checks: list, name: str, path: pathlib.Path | None, hint: str | None = None) -> None:
    exists = path is not None and path.exists()
    checks.append(
        {
            "name": name,
            "ok": exists,
            "error": None if exists else f"File not found: {path if path is not None else '<missing>'}",
            "hint": hint if not exists else None,
        }
    )


def _check_writable(project_dir: pathlib.Path) -> dict:
    name = "Output paths writable"
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        test_file = project_dir / ".composer_write_test"
        test_file.write_text("x", encoding="utf-8")
        test_file.unlink()
        return {"name": name, "ok": True, "error": None, "hint": None}
    except OSError as exc:
        return {
            "name": name,
            "ok": False,
            "error": f"{exc} (path: {project_dir})",
            "hint": "Check directory permissions or available disk space.",
        }


def _check_config_binary(check_name: str, exe: pathlib.Path | None, yaml_path: pathlib.Path) -> dict:
    if exe is None or not exe.exists():
        return {"name": check_name, "ok": None, "note": "Binary missing — skipped"}
    if not yaml_path.exists():
        return {"name": check_name, "ok": None, "note": "Config not yet rendered"}
    try:
        result = subprocess.run(
            [str(exe), "--check-config", str(yaml_path)],
            cwd=str(paths_module.DATA_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            env=child_env(),
        )
        if result.returncode == 0:
            return {"name": check_name, "ok": True, "error": None, "hint": None}
        stderr = result.stderr or ""
        # Only the binary refusing the flag itself means the check cannot run:
        # a line that names --check-config as unknown ("Unknown argument:
        # --check-config" from the runtime, "unknown option: --check-config"
        # elsewhere). "unknown" on any other line, such as the runtime's
        # unknown-key warning, is part of a config it refused (#852).
        if any(
            "--check-config" in line and any(kw in line for kw in ("unknown", "unrecognized", "invalid option"))
            for line in stderr.lower().splitlines()
        ):
            return {"name": check_name, "ok": None, "note": "Not yet available"}
        # The last ERROR: line, in full, is the reason the config was refused.
        error_lines = [line.strip() for line in stderr.splitlines() if line.strip().startswith("ERROR:")]
        error = error_lines[-1] if error_lines else (stderr or result.stdout or "Non-zero exit").strip()
        return {"name": check_name, "ok": False, "error": error, "hint": None}
    except subprocess.TimeoutExpired:
        return {"name": check_name, "ok": False, "error": "Timed out (10s)", "hint": None}
    except OSError as exc:
        return {"name": check_name, "ok": False, "error": str(exc), "hint": None}


def _check_host_binary(check_name: str, exe: pathlib.Path | None, yaml_path: pathlib.Path) -> dict:
    """Run a provider's --check-host as the user the dev launch runs as.

    Exit 0 is met (unknown requirements do not fail), 1 is unmet, 2 could not
    evaluate. A non-zero exit with no JSON on stdout is no answer — a provider
    that predates the verb — and is skipped, as install.sh's preflight does.
    """
    if exe is None or not exe.exists():
        return {"name": check_name, "ok": None, "note": "Binary missing — skipped"}
    if not yaml_path.exists():
        return {"name": check_name, "ok": None, "note": "Config not yet rendered"}
    try:
        result = subprocess.run(
            [str(exe), "--check-host", str(yaml_path)],
            cwd=str(paths_module.DATA_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            env=child_env(),
        )
    except subprocess.TimeoutExpired:
        return {"name": check_name, "ok": False, "error": "Timed out (10s)", "hint": None}
    except OSError as exc:
        return {"name": check_name, "ok": False, "error": str(exc), "hint": None}

    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        envelope = None
    requirements = envelope.get("requirements") if isinstance(envelope, dict) else None
    if not isinstance(requirements, list):
        if result.returncode == 0:
            return {"name": check_name, "ok": None, "note": "No host requirements reported"}
        return {"name": check_name, "ok": None, "note": "Not yet available"}

    reqs = [r for r in requirements if isinstance(r, dict)]
    unmet = [r for r in reqs if r.get("status") == "unmet"]
    unknown = [r for r in reqs if r.get("status") == "unknown"]
    note = "; ".join(f"{r.get('id', '?')} unknown: {r.get('detail', '')}" for r in unknown) or None
    if result.returncode == 0:
        return {"name": check_name, "ok": True, "error": None, "hint": None, "note": note}
    if result.returncode == 1:
        return {
            "name": check_name,
            "ok": False,
            "error": "; ".join(f"{r.get('id', '?')}: {r.get('detail', '')}" for r in unmet) or "unmet",
            "hint": "; ".join(str(r["remedy"]) for r in unmet if r.get("remedy")) or None,
            "note": note,
        }
    error = (result.stderr or "").strip()[:200] or f"exit {result.returncode}"
    return {"name": check_name, "ok": False, "error": f"Could not evaluate host requirements: {error}", "hint": None}


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


class LaunchConfigError(RuntimeError):
    """An executable the project names is not set or cannot be run (#672).

    The routes answer it with 422 and `code`, not with the 409 a runtime that
    is already running gets: the caller has a project to fix, not one to stop.
    """

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class LaunchValidationError(RuntimeError):
    """The project fails the blocking validation preflight runs (#414)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("Project failed validation; nothing was started or stopped.")
        self.errors = errors


def launch(name: str, system: dict, project_dir: pathlib.Path) -> None:
    """Start the anolis-runtime subprocess."""
    with _lifecycle_lock:
        _launch_locked(name, system, project_dir)


def _launch_locked(name: str, system: dict, project_dir: pathlib.Path) -> None:
    current = _current_runtime_snapshot(clean_stale=True)
    if current["running"]:
        raise RuntimeError("A system is already running.")

    # The same blocking validation as preflight's "System-level validation":
    # requiring a passing preflight is enforced only in the Commission UI, so
    # a direct launch request must not start a project preflight would refuse
    # (#414). Preflight's binary checks stay in preflight.
    from anolis_workbench.core import canonical_validator  # local import, as in preflight

    errors = canonical_validator.validate_project(system)
    if errors:
        raise LaunchValidationError(errors)

    # Project the canonical variant into a runnable host config.
    launch_config = str(materialize_launch_config(project_dir, system))

    _spawn(name, system, project_dir, launch_config)


def _runtime_command(system: dict, launch_config: str) -> list[str]:
    host = canonical.host_paths({"host_paths": system.get("host_paths")})
    runtime_exe = _resolve_executable_path(host.get("runtime_executable", ""))
    if runtime_exe is None:
        raise LaunchConfigError(
            "Runtime executable path is missing (set it in the project's host paths).",
            code="runtime_executable_missing",
        )
    # Checked here, not left to Popen, so restart() can refuse it before it
    # stops the running runtime (#672).
    if not runtime_exe.is_file() or not os.access(runtime_exe, os.X_OK):
        raise LaunchConfigError(
            f"Runtime executable '{runtime_exe}' does not exist or is not executable "
            "(set it in the project's host paths).",
            code="runtime_executable_missing",
        )
    return [str(runtime_exe), "--config", launch_config]


def _detached_process_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _spawn(name: str, system: dict, project_dir: pathlib.Path, launch_config: str) -> None:
    """Start the runtime on `launch_config` and publish it as the tracked process.

    A failure after the runtime has started stops it again, so nothing runs
    that the workbench does not track (#671).
    """
    cmd = _runtime_command(system, launch_config)

    # The runtime writes its log file itself. A pipe the server read had no
    # reader once the server exited, and a runtime left running after a
    # desktop quit (#916) lost its providers: they share its stderr and die
    # of SIGPIPE on their next write. The server follows the file instead.
    #
    # The file is opened for append, so a truncation under a live writer
    # cannot leave a run of NULs (review of #932). The last run's log is
    # copied aside, not moved: no file another process holds open is renamed,
    # which Windows refuses. The copy becomes previous.log only once the
    # runtime has started; a failed launch puts latest.log back and leaves
    # previous.log as it was (#672, review of #932).
    log_dir = project_dir / "logs"
    log_path = log_dir / "latest.log"
    previous_path = log_dir / "previous.log"
    kept_path = log_dir / "latest.log.kept"
    log_dir.mkdir(parents=True, exist_ok=True)
    had_log = log_path.is_file()
    if had_log:
        shutil.copyfile(log_path, kept_path)
    try:
        fd = os.open(
            log_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND | getattr(os, "O_BINARY", 0),
            0o644,
        )
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=fd,
                stderr=subprocess.STDOUT,
                cwd=str(paths_module.DATA_ROOT),
                env=child_env(),
                # Its own session (process group on Windows): a Ctrl-C in the
                # terminal that started the app or server must not reach a
                # runtime that outlives them (review of #932). Stop signals it
                # directly.
                **_detached_process_kwargs(),
            )
        finally:
            os.close(fd)
    except BaseException:
        try:
            if had_log:
                shutil.copyfile(kept_path, log_path)
                kept_path.unlink()
            else:
                log_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if had_log:
        try:
            os.replace(kept_path, previous_path)
        except OSError:
            pass  # previous.log stays one run older; the launch goes on

    try:
        # Write running.json
        running_data = {
            "pid": proc.pid,
            "project": name,
            "started": datetime.now(timezone.utc).isoformat(),
        }
        (project_dir / "running.json").write_text(json.dumps(running_data), encoding="utf-8")

        with _state_lock:
            log_lines_by_project: dict = _state["log_lines_by_project"]
            log_lines_by_project[name] = []
            _state.update(
                {
                    "project": name,
                    "process": proc,
                }
            )
        threading.Thread(target=_read_logs, args=(proc, log_path, name), daemon=True).start()
    except BaseException:
        with _state_lock:
            if _state["process"] is proc:
                _state.update({"project": None, "process": None})
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


def stop(name: str) -> None:
    """Gracefully terminate project `name`'s runtime; a no-op when none runs.

    Refuses, as restart() does, when the running runtime belongs to another
    project, which a stale page or a second tab could otherwise end (#670).
    """
    with _lifecycle_lock:
        current = _current_runtime_snapshot(clean_stale=False)
        if current["running"] and current["project"] != name:
            raise RuntimeError(
                f"Cannot stop '{name}' while '{current['project']}' is running. "
                f"Stop '{current['project']}' from its own project."
            )
        _stop_locked()


def _stop_locked() -> None:
    with _state_lock:
        proc = _state["process"]
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return

    detached = _discover_running_runtime(clean_stale=False)
    if detached is None:
        return

    _terminate_pid(detached["pid"])
    if not _pid_exists(detached["pid"]):
        try:
            running_json_path(detached["project"]).unlink(missing_ok=True)
        except OSError:
            pass
        _notify_sse_subscribers(detached["project"], None)


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------


def restart(name: str, project_dir: pathlib.Path) -> None:
    """Kill and relaunch the running project.

    Re-reads the saved project and renders the launch variant's YAML again,
    as launch() does. So the saved project is validated first, as launch()
    does, before the running runtime is stopped (#414).
    """
    with _lifecycle_lock:
        _restart_locked(name, project_dir)


def _restart_locked(name: str, project_dir: pathlib.Path) -> None:
    current = _current_runtime_snapshot(clean_stale=True)
    if not current["running"]:
        raise RuntimeError(f"Cannot restart '{name}' because no project is running.")
    if current.get("external"):
        raise RuntimeError(
            f"'{name}' is running as an external service (e.g. systemd) that the workbench "
            "does not manage — restart it on the device (e.g. systemctl restart anolis-runtime)."
        )
    if current["project"] != name:
        raise RuntimeError(
            f"Cannot restart '{name}' while '{current['project']}' is running. Stop '{current['project']}' first."
        )

    from anolis_workbench.core import canonical_validator  # local import, as in preflight
    from anolis_workbench.core import projects as projects_module

    system = projects_module.get_project(name)
    # Refuse before stop(), so a refused restart leaves the runtime running.
    errors = canonical_validator.validate_project(system)
    if errors:
        raise LaunchValidationError(errors)
    # Rendered again, as launch() does, so a file an older workbench wrote is
    # never handed to the runtime (it may lack runtime.data_dir, #562). Before
    # stop(), so a project that cannot render leaves the runtime running.
    launch_config = str(materialize_launch_config(project_dir, system))
    # And one with no runtime executable to start, too (#672).
    _runtime_command(system, launch_config)

    _stop_locked()
    time.sleep(0.5)  # Allow log reader thread to flush and clean up

    _spawn(name, system, project_dir, launch_config)


# ---------------------------------------------------------------------------
# Log reader thread
# ---------------------------------------------------------------------------


def _publish_log_line(project: str, line: str) -> None:
    with _state_lock:
        lines = _state["log_lines_by_project"].setdefault(project, [])
        lines.append(line)
        if len(lines) > 200:
            lines.pop(0)
    _notify_sse_subscribers(project, line)


def _follow_log(
    log_path: pathlib.Path,
    project: str,
    exited: Callable[[], bool],
    poll_s: float = 0.2,
    tail_bytes: int | None = None,
) -> None:
    """Publish each line the runtime writes to its log until `exited()` is true.

    With `tail_bytes`, start that far from the end, at the next full line, so
    a log a runtime has written for weeks is not replayed whole.
    """
    try:
        with open(log_path, "rb") as log:
            if tail_bytes is not None:
                size = log.seek(0, os.SEEK_END)
                log.seek(max(0, size - tail_bytes))
                if size > tail_bytes:
                    log.readline()  # the rest of a cut line
            partial = b""
            while True:
                chunk = log.readline()
                if chunk:
                    partial += chunk
                    if partial.endswith(b"\n"):
                        _publish_log_line(project, partial.decode("utf-8", errors="replace"))
                        partial = b""
                    continue
                if exited():
                    rest = partial + log.read()
                    for line in rest.decode("utf-8", errors="replace").splitlines(keepends=True):
                        _publish_log_line(project, line)
                    return
                time.sleep(poll_s)
    except OSError:
        while not exited():
            time.sleep(poll_s)


def _read_logs(proc: subprocess.Popen, log_path: pathlib.Path, project: str, poll_s: float = 0.2) -> None:
    """Follow the log file the runtime writes until the runtime exits."""
    _follow_log(log_path, project, lambda: proc.poll() is not None, poll_s)
    _notify_sse_subscribers(project, None)  # sentinel: the runtime has exited
    _cleanup_after_exit(proc)


# A runtime this server did not start, found through running.json: a server
# reopened after the desktop app quit followed nothing, and its log pane
# stayed empty (review of #932). One follower per (project, pid).
_detached_followers: set[tuple[str, int]] = set()
_detached_followers_lock = threading.Lock()


def _follow_detached(project: str, pid: int) -> None:
    key = (project, pid)
    with _detached_followers_lock:
        if key in _detached_followers:
            return
        _detached_followers.add(key)

    def _run() -> None:
        try:
            _follow_log(
                running_json_path(project).parent / "logs" / "latest.log",
                project,
                lambda: not _pid_exists(pid),
                tail_bytes=64 * 1024,
            )
            _notify_sse_subscribers(project, None)
        finally:
            with _detached_followers_lock:
                _detached_followers.discard(key)

    threading.Thread(target=_run, name=f"log-follow-{project}", daemon=True).start()


def _notify_sse_subscribers(project: str, line) -> None:
    with _sse_lock:
        subscribers = list(_sse_subscribers.get(project, []))
    for q in subscribers:
        try:
            q.put_nowait(line)
        except Exception:
            pass


def _cleanup_after_exit(proc: subprocess.Popen) -> None:
    with _state_lock:
        # Guard: don't clobber state if a new process was already started
        if _state["process"] is not proc:
            return
        project = _state["project"]
        _state.update({"process": None, "project": None})
    if project:
        rj = running_json_path(project)
        try:
            rj.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# SSE log stream
# ---------------------------------------------------------------------------


def handle_log_stream(handler, project_name: str) -> None:
    """Stream log output to the browser via Server-Sent Events.

    ``handler`` is the BaseHTTPRequestHandler instance (provides .wfile).
    Blocks until the client disconnects or the process exits.
    """
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    # Replay buffered lines first (reconnect support)
    with _state_lock:
        buffered = list(_state["log_lines_by_project"].get(project_name, []))
    for line in buffered:
        try:
            handler.wfile.write(f"data: {line.rstrip()}\n\n".encode("utf-8"))
            handler.wfile.flush()
        except OSError:
            return

    # Subscribe to live output
    q: queue.Queue = queue.Queue(maxsize=500)
    with _sse_lock:
        _sse_subscribers.setdefault(project_name, []).append(q)

    try:
        while True:
            try:
                line = q.get(timeout=15)
            except queue.Empty:
                # Keepalive heartbeat (SSE comment — ignored by EventSource)
                try:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
                except OSError:
                    return
                continue

            if line is None:  # sentinel: process exited
                try:
                    handler.wfile.write(b"data: [process exited]\n\n")
                    handler.wfile.flush()
                except OSError:
                    pass
                return

            try:
                handler.wfile.write(f"data: {line.rstrip()}\n\n".encode("utf-8"))
                handler.wfile.flush()
            except OSError:
                return

    finally:
        with _sse_lock:
            try:
                subscribers = _sse_subscribers.get(project_name, [])
                subscribers.remove(q)
                if not subscribers:
                    _sse_subscribers.pop(project_name, None)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Dev-launch host projection (#255)
# ---------------------------------------------------------------------------


def launch_variant(document: dict) -> str:
    launch = document.get("launch")
    variant = launch.get("variant") if isinstance(launch, dict) else None
    if isinstance(variant, str) and variant in (document.get("variants") or {}):
        return variant
    variants = document.get("variants") or {}
    return canonical.MANUAL_VARIANT if canonical.MANUAL_VARIANT in variants else next(iter(variants), "")


def active_runtime_config(document: dict) -> dict | None:
    variant = launch_variant(document)
    doc = (document.get("variants") or {}).get(variant)
    return doc if isinstance(doc, dict) else None


def _holds_runtime_data(data_dir: pathlib.Path) -> bool:
    """A latch file or a non-empty run index; the runtime creates runs/ at every start."""
    index = data_dir / "runs" / "index.jsonl"
    return any((data_dir / "estop").glob("*.json")) or (index.is_file() and index.stat().st_size > 0)


def _relative_data_dir(value: str) -> pathlib.Path:
    """Where a launch keeps a variant's relative runtime.data_dir.

    A variant is written for an install, whose config is
    <prefix>/config/runtime.yaml (anolishq/anolis#643), so it is resolved as if
    DATA_ROOT were the prefix: ../anolis-data is DATA_ROOT/anolis-data. One whose
    data is only at DATA_ROOT/<it>, where a launch put it before, stays there.
    """
    beside = pathlib.Path(os.path.normpath(paths_module.DATA_ROOT / "config" / value))
    before = pathlib.Path(os.path.normpath(paths_module.DATA_ROOT / value))
    if not _holds_runtime_data(beside) and _holds_runtime_data(before):
        return before
    return beside


def materialize_launch_config(project_dir: pathlib.Path, document: dict) -> pathlib.Path:
    """Project the canonical variant into a runnable host config.

    The canonical files carry DEPLOY TOKENS, which only mean something after
    install.sh rewrites them. Dev-launch needs real host paths, so we write a
    throwaway copy under .workbench/launch/ with the sidecar's host paths
    substituted in. The canonical files are never modified — that separation is
    what lets one artifact set serve both deploy and dev-launch.
    """
    runtime_doc = active_runtime_config(document)
    if runtime_doc is None:
        raise RuntimeError("Project has no runtime variant to launch.")

    host = canonical.host_paths({"host_paths": document.get("host_paths")})
    provider_paths = host.get("providers", {})
    projected = copy.deepcopy(runtime_doc)

    profile_providers = document.get("profile", {}).get("providers", {})
    for entry in projected.get("providers", []):
        if not isinstance(entry, dict):
            continue
        pid = entry.get("id")
        executable = provider_paths.get(pid, {}).get("executable") if isinstance(pid, str) else None
        if not executable:
            raise LaunchConfigError(
                f"No host executable configured for provider '{pid}' (set it in the project's paths).",
                code="provider_executable_missing",
            )
        entry["command"] = str(_resolve_executable_path(executable) or executable)
        rel = profile_providers.get(pid, {}).get("config") if isinstance(pid, str) else None
        if isinstance(rel, str):
            entry["args"] = ["--config", str((project_dir / rel).resolve())]

    automation = projected.get("automation")
    if isinstance(automation, dict):
        behavior = automation.get("behavior_tree")
        if isinstance(behavior, str) and behavior.startswith("../"):
            match = canonical.PROJECT_PATH_RE.fullmatch(behavior)
            if match is not None:
                automation["behavior_tree"] = str((project_dir / match.group(2) / match.group(3)).resolve())

    # The runtime resolves a default or relative runtime.data_dir against the
    # config file's directory (anolishq/anolis#562), which is inside the
    # project, and a re-provision replaces the project directory. Name the
    # directory a launch has always used, since the runtime starts in
    # DATA_ROOT, so the e-stop latch and the run journal stay where they are.
    runtime_section = projected.get("runtime")
    if not isinstance(runtime_section, dict):
        runtime_section = {}
        projected = {"runtime": runtime_section, **{k: v for k, v in projected.items() if k != "runtime"}}
    data_dir = runtime_section.get("data_dir")
    if not data_dir:
        runtime_section["data_dir"] = str(paths_module.DATA_ROOT / "anolis-data")
    elif not pathlib.Path(str(data_dir)).is_absolute():
        runtime_section["data_dir"] = str(_relative_data_dir(str(data_dir)))

    out = project_dir / canonical.LAUNCH_DIR / "launch"
    out.mkdir(parents=True, exist_ok=True)
    target = out / canonical.variant_filename(launch_variant(document))
    target.write_text(yaml.safe_dump(projected, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return target
