"""Self-update says how to upgrade instead of running anything (#535).

The workbench installs as a PyPI wheel (pipx, or a venv such as the Pi
script's) or as a desktop bundle, and no workbench release has the install.sh
the old self-update downloaded and ran under sudo. The update check now names
the command that upgrades the install it runs from; nothing is downloaded or
executed.
"""

from __future__ import annotations

import http.client
import json
import pathlib
import shlex
import subprocess
import sys
import threading
from collections.abc import Generator
from typing import Any

import pytest

from anolis_workbench.core import updater


@pytest.fixture(autouse=True)
def _nothing_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"self-update ran a command: {args}")

    monkeypatch.setattr(subprocess, "run", _refuse)
    monkeypatch.setattr(subprocess, "Popen", _refuse)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)


def _install(monkeypatch: pytest.MonkeyPatch, prefix: str, executable: str) -> None:
    monkeypatch.setattr(sys, "prefix", prefix)
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.setattr(updater, "_has_pip", lambda: True)


def test_a_venv_install_upgrades_with_that_venvs_pip(monkeypatch):
    _install(
        monkeypatch,
        "/home/pi/.local/share/anolis-workbench/venv",
        "/home/pi/.local/share/anolis-workbench/venv/bin/python3",
    )
    got = updater.upgrade_instructions("0.33.0")
    assert got.install == "pip"
    assert got.command == (
        "/home/pi/.local/share/anolis-workbench/venv/bin/python3 -m pip install --upgrade anolis-workbench==0.33.0"
    )


def test_a_path_with_spaces_is_quoted(monkeypatch):
    _install(monkeypatch, "/opt/my venv", "/opt/my venv/bin/python")
    got = updater.upgrade_instructions("0.33.0")
    assert got.command is not None
    assert shlex.split(got.command)[0] == "/opt/my venv/bin/python"


def test_without_a_known_version_the_pin_is_left_off(monkeypatch):
    _install(monkeypatch, "/srv/wb/venv", "/srv/wb/venv/bin/python")
    assert (
        updater.upgrade_instructions(None).command
        == "/srv/wb/venv/bin/python -m pip install --upgrade anolis-workbench"
    )


@pytest.mark.parametrize(
    "prefix",
    ["/home/u/.local/share/pipx/venvs/anolis-workbench", "/home/u/.local/pipx/venvs/anolis-workbench"],
)
def test_a_pipx_install_upgrades_with_pipx(monkeypatch, prefix):
    _install(monkeypatch, prefix, f"{prefix}/bin/python")
    got = updater.upgrade_instructions("0.33.0")
    assert got.install == "pipx"
    assert got.command == "pipx upgrade anolis-workbench"


def test_a_uv_tool_install_upgrades_with_uv(monkeypatch):
    prefix = "/home/u/.local/share/uv/tools/anolis-workbench"
    _install(monkeypatch, prefix, f"{prefix}/bin/python")
    got = updater.upgrade_instructions("0.33.0")
    assert got.install == "uv-tool"
    assert got.command == "uv tool upgrade anolis-workbench"


def test_a_venv_without_pip_upgrades_with_uv_pip(monkeypatch):
    # uv makes venvs without pip, where `python -m pip` fails.
    _install(monkeypatch, "/srv/wb/.venv", "/srv/wb/.venv/bin/python")
    monkeypatch.setattr(updater, "_has_pip", lambda: False)
    got = updater.upgrade_instructions("0.33.0")
    assert got.install == "pip"
    assert got.command == "uv pip install --python /srv/wb/.venv/bin/python --upgrade anolis-workbench==0.33.0"


def test_the_desktop_app_points_at_the_release_and_has_no_command(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    got = updater.upgrade_instructions("0.33.0")
    assert got.install == "desktop"
    assert got.command is None and got.restart is None
    assert "https://github.com/anolishq/anolis-workbench/releases/tag/v0.33.0" in got.note


def test_under_systemd_the_restart_names_the_service(monkeypatch):
    _install(monkeypatch, "/srv/wb/venv", "/srv/wb/venv/bin/python")
    monkeypatch.setenv("INVOCATION_ID", "0123abcd")
    assert updater.upgrade_instructions("0.33.0").restart == "sudo systemctl restart anolis-workbench"


def test_outside_systemd_there_is_no_restart_command(monkeypatch):
    _install(monkeypatch, "/srv/wb/venv", "/srv/wb/venv/bin/python")
    got = updater.upgrade_instructions("0.33.0")
    assert got.restart is None
    assert "restart" in got.note.lower()


def test_the_download_and_run_path_is_gone():
    for name in ("perform_update", "download_install_script", "UpdateResult"):
        assert not hasattr(updater, name), name


# --- over HTTP and the CLI -------------------------------------------------


@pytest.fixture
def server(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Generator[int, None, None]:
    from anolis_workbench.core import projects as projects_module
    from anolis_workbench.server import app

    systems = tmp_path / "systems"
    systems.mkdir()
    monkeypatch.setattr(projects_module, "SYSTEMS_ROOT", systems)
    monkeypatch.setattr(updater, "fetch_latest_version", lambda: "99.0.0")
    _install(monkeypatch, "/srv/wb/venv", "/srv/wb/venv/bin/python")

    wb = app._WorkbenchServer(("127.0.0.1", 0), app._Handler)
    wb.config = app.resolve_config("127.0.0.1", 0, False)
    threading.Thread(target=wb.serve_forever, daemon=True).start()
    try:
        yield wb.server_address[1]
    finally:
        wb.shutdown()
        wb.server_close()


def _request(port: int, method: str, path: str, body: bytes | None = None) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def test_the_update_check_carries_the_upgrade_command(server):
    code, body = _request(server, "GET", "/api/update-check")
    assert code == 200
    assert body["update_available"] is True and body["latest_version"] == "99.0.0"
    upgrade = body["upgrade"]
    assert upgrade["install"] == "pip"
    assert upgrade["command"] == "/srv/wb/venv/bin/python -m pip install --upgrade anolis-workbench==99.0.0"
    assert upgrade["restart"] is None
    assert isinstance(upgrade["note"], str) and upgrade["note"]


def test_post_update_is_gone_and_names_the_command(server):
    code, body = _request(server, "POST", "/api/update", b"{}")
    assert code == 410
    assert "anolis-workbench==99.0.0" in body["error"]
    assert body["upgrade"]["command"] == "/srv/wb/venv/bin/python -m pip install --upgrade anolis-workbench==99.0.0"


@pytest.mark.parametrize("command", ["check-update", "update"])
def test_the_cli_prints_the_command_and_runs_nothing(monkeypatch, capsys, command):
    from anolis_workbench.cli import provision_cli

    monkeypatch.setattr(updater, "fetch_latest_version", lambda: "99.0.0")
    _install(monkeypatch, "/srv/wb/venv", "/srv/wb/venv/bin/python")
    monkeypatch.setattr(sys, "argv", ["anolis-provision", command])
    assert provision_cli.main() == 0
    out = capsys.readouterr().out
    assert "/srv/wb/venv/bin/python -m pip install --upgrade anolis-workbench==99.0.0" in out
    assert "install.sh" not in out
