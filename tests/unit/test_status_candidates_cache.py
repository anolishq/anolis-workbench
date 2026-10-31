"""wb#754: /api/status finds an external runtime without loading every project.

launcher.get_status() looked for a runtime the workbench did not launch by
loading each project's document and installed config to learn its port, then
probing it, on every call: the header polls it every 2 s, and on the rig a
call took 0.10-0.32 s. The ports to probe (the candidates) are now kept until
a file they were read from changes. The probe itself still runs on every
call, so a runtime that stops or starts shows at once.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

import pytest

from anolis_workbench.core import installed_project, launcher
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module


@pytest.fixture()
def machine(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Projects "other" (port 9001 from its document) and "rig" (installed,
    port 8085 from the prefix's config), with every lookup counted."""
    systems = tmp_path / "systems"
    for name in ("other", "rig"):
        (systems / name / "config").mkdir(parents=True)
        (systems / name / "machine-profile.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        (systems / name / "config" / "runtime.yaml").write_text("http: {port: 9001}\n", encoding="utf-8")
    prefix = tmp_path / "opt"
    (prefix / "config").mkdir(parents=True)
    (prefix / "config" / "runtime.yaml").write_text("http: {port: 8085}\n", encoding="utf-8")

    monkeypatch.setattr(paths_module, "SYSTEMS_ROOT", systems)
    monkeypatch.setattr(projects_module, "SYSTEMS_ROOT", systems)
    monkeypatch.setattr(launcher, "_state", {"process": None, "project": None, "log_file": None})
    monkeypatch.setattr(launcher, "_candidates", None, raising=False)

    counts = {"project": 0, "installed_port": 0, "probe": 0}
    alive: set[tuple[str, int]] = {("127.0.0.1", 8085)}
    ports = {"other": 9001, "installed": 8085}

    def get_project(name: str) -> dict:
        counts["project"] += 1
        return {"name": name}

    def installed_runtime_port(path: pathlib.Path) -> int | None:
        counts["installed_port"] += 1
        return ports["installed"]

    def runtime_alive(bind: str, port: int, timeout_s: float = 0.5) -> bool:
        counts["probe"] += 1
        return (bind, port) in alive

    monkeypatch.setattr(projects_module, "get_project", get_project)
    monkeypatch.setattr(projects_module, "installed_from", lambda name: str(prefix) if name == "rig" else None)
    monkeypatch.setattr(installed_project, "installed_runtime_port", installed_runtime_port)
    monkeypatch.setattr(
        launcher, "active_runtime_config", lambda doc: {"http": {"bind": "127.0.0.1", "port": ports["other"]}}
    )
    monkeypatch.setattr(launcher, "_runtime_alive", runtime_alive)
    return {"counts": counts, "alive": alive, "ports": ports, "systems": systems, "prefix": prefix}


def _touch(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def _status() -> dict:
    return launcher.get_status()


def test_status_polls_load_the_projects_once_and_probe_every_time(machine: dict[str, Any]) -> None:
    for _ in range(5):
        status = _status()
        assert (status["running"], status["active_project"], status["external"]) == (True, "rig", True)

    assert machine["counts"]["project"] == 1
    assert machine["counts"]["installed_port"] == 1
    assert machine["counts"]["probe"] == 10  # "other" and "rig", on each call


def test_a_runtime_that_stops_shows_at_once(machine: dict[str, Any]) -> None:
    _status()
    machine["alive"].clear()

    assert _status()["running"] is False
    assert machine["counts"]["project"] == 1


def test_a_runtime_that_starts_shows_at_once(machine: dict[str, Any]) -> None:
    machine["alive"].clear()
    assert _status()["running"] is False
    machine["alive"].add(("127.0.0.1", 9001))

    status = _status()

    assert (status["running"], status["active_project"]) == (True, "other")
    assert machine["counts"]["project"] == 1


@pytest.mark.parametrize("change", ["a project's config", "a project's profile", "the installed config"])
def test_a_change_to_a_file_the_ports_were_read_from_reads_them_again(machine: dict[str, Any], change: str) -> None:
    _status()
    machine["alive"].clear()
    machine["ports"].update(other=9002, installed=8086)
    machine["alive"].add(("127.0.0.1", 9002 if change != "the installed config" else 8086))
    {
        "a project's config": lambda: _touch(machine["systems"] / "other" / "config" / "runtime.yaml", "x\n"),
        "a project's profile": lambda: _touch(machine["systems"] / "other" / "machine-profile.yaml", "y\n"),
        "the installed config": lambda: _touch(machine["prefix"] / "config" / "runtime.yaml", "z\n"),
    }[change]()

    assert _status()["running"] is True
    assert machine["counts"]["project"] == 2
