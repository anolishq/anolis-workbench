"""Integration test: the frozen sidecar serves a healthy ``/api/status``.

The freeze-smoke CI lane builds ``dist/anolis-workbench`` and points
``ANOLIS_WORKBENCH_SIDECAR_BIN`` at it. This test boots that artifact as a
black box and asserts its health-check endpoint comes up and answers — the
``--version``/``--help`` checks alone never exercise the running server.

When the env var is unset (the default ``pytest tests/`` lane, local runs),
the test skips, so it never fails without a frozen binary to point at.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

SIDECAR_BIN_ENV = "ANOLIS_WORKBENCH_SIDECAR_BIN"
_BOOT_TIMEOUT_S = 30.0
_SHUTDOWN_TIMEOUT_S = 10.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _sidecar_binary() -> Path:
    raw = os.getenv(SIDECAR_BIN_ENV)
    if not raw:
        pytest.skip(f"{SIDECAR_BIN_ENV} not set; build the frozen sidecar first")
    path = Path(raw)
    if not path.exists():
        pytest.skip(f"frozen sidecar not found at {path}")
    return path


def test_frozen_sidecar_reports_healthy_status() -> None:
    binary = _sidecar_binary()
    port = _free_port()

    # The CLI flags bind HOST/PORT from module globals read at import time, so
    # configure the process through the environment, which app.py reads on boot.
    env = {
        **os.environ,
        "ANOLIS_WORKBENCH_HOST": "127.0.0.1",
        "ANOLIS_WORKBENCH_PORT": str(port),
        "ANOLIS_WORKBENCH_OPEN_BROWSER": "0",
    }
    proc = subprocess.Popen(
        [str(binary)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    url = f"http://127.0.0.1:{port}/api/status"
    try:
        payload = _poll_status(proc, url, deadline=time.monotonic() + _BOOT_TIMEOUT_S)
        # Health-check contract: 200 with the bound composer address echoed back
        # plus the workbench marker that get_status() always emits.
        assert payload["composer"]["port"] == port
        assert payload["workbench"]["version"] == 1
        assert "running" in payload
    finally:
        _terminate(proc)


def _poll_status(proc: subprocess.Popen, url: str, *, deadline: float) -> dict:
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            raise AssertionError(f"sidecar exited early (code {proc.returncode}):\n{out}")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 (localhost)
                assert resp.status == 200, f"unexpected status {resp.status}"
                payload: dict = json.loads(resp.read().decode())
                return payload
        except (urllib.error.URLError, ConnectionError) as exc:
            last_err = exc
            time.sleep(0.25)
    raise AssertionError(f"/api/status never became reachable: {last_err}")


def _terminate(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=_SHUTDOWN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=_SHUTDOWN_TIMEOUT_S)
