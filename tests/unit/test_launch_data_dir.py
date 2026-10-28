"""The runtime's data dir in the launch config (anolishq/anolis#562).

The runtime keeps its e-stop latch and run journal under runtime.data_dir. From
the anolis release that fixes #562 it resolves a default or relative data_dir
against the config file's directory, which for a launch is
<project>/.workbench/launch/, and re-provisioning replaces the whole project
directory. The launch config therefore names the directory a launch has always
used, DATA_ROOT/anolis-data (the runtime starts in DATA_ROOT), so the latch
and the journal stay where they are and survive a re-provision.

A relative data_dir in a variant is written for an install, where the config
is <prefix>/config/runtime.yaml (anolishq/anolis#643): a launch resolves it as
if DATA_ROOT were the prefix, so ../anolis-data is DATA_ROOT/anolis-data. One
whose data is at DATA_ROOT/<it>, where a launch put it before, stays there.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml

from anolis_workbench.core import canonical, installer, launcher


def _launch_runtime_section(project_dir: pathlib.Path) -> dict:
    target = launcher.materialize_launch_config(project_dir, canonical.read_project(project_dir))
    runtime: dict = yaml.safe_load(target.read_text(encoding="utf-8"))["runtime"]
    return runtime


@pytest.fixture
def rig(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    data_root = tmp_path / "dot-anolis"
    systems_root = data_root / "systems"
    systems_root.mkdir(parents=True)
    monkeypatch.setattr("anolis_workbench.core.paths.DATA_ROOT", data_root)
    monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
    monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)
    source = tmp_path / "clone"
    shutil.copytree(pathlib.Path(__file__).parent.parent / "fixtures" / "mixed-bus-mock", source)
    return {"data_root": data_root, "systems_root": systems_root, "source": source, "prefix": tmp_path / "prefix"}


def _provision(rig: dict, *, force: bool = False) -> pathlib.Path:
    return installer.provision_project(
        "unused-when-system-path-given",
        "mixed-rig",
        rig["prefix"],
        force=force,
        system_path=rig["source"],
        systems_root=rig["systems_root"],
    )


def _set_data_dir(project_dir: pathlib.Path, value: str) -> None:
    variant = project_dir / canonical.variant_relpath(canonical.MANUAL_VARIANT)
    doc = yaml.safe_load(variant.read_text(encoding="utf-8"))
    doc.setdefault("runtime", {})["data_dir"] = value
    variant.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _write_latch(data_dir: pathlib.Path) -> None:
    (data_dir / "estop").mkdir(parents=True, exist_ok=True)
    (data_dir / "estop" / "0123456789abcdef.json").write_text('{"latched":true}\n', encoding="utf-8")


def test_a_launch_names_the_data_dir_a_launch_has_always_used(rig: dict) -> None:
    project_dir = _provision(rig)

    runtime = _launch_runtime_section(project_dir)

    assert runtime["data_dir"] == str(rig["data_root"] / "anolis-data")
    assert not pathlib.Path(runtime["data_dir"]).is_relative_to(project_dir)


def test_a_relative_data_dir_is_pinned_to_where_the_launch_resolved_it(rig: dict) -> None:
    project_dir = _provision(rig)
    _set_data_dir(project_dir, "state/data")
    _write_latch(rig["data_root"] / "state" / "data")

    assert _launch_runtime_section(project_dir)["data_dir"] == str(rig["data_root"] / "state" / "data")


def test_a_variant_naming_the_install_data_dir_launches_in_the_launch_data_dir(rig: dict) -> None:
    # bioreactor-v1's variants name ../anolis-data, <prefix>/anolis-data when
    # installed; a launch keeps its latch where it always has (anolis#643).
    project_dir = _provision(rig)
    _set_data_dir(project_dir, "../anolis-data")

    assert _launch_runtime_section(project_dir)["data_dir"] == str(rig["data_root"] / "anolis-data")


def test_a_relative_data_dir_with_no_data_yet_resolves_as_installed(rig: dict) -> None:
    project_dir = _provision(rig)
    _set_data_dir(project_dir, "state/data")

    assert _launch_runtime_section(project_dir)["data_dir"] == str(rig["data_root"] / "config" / "state" / "data")


def test_a_relative_data_dir_with_data_in_both_places_resolves_as_installed(rig: dict) -> None:
    project_dir = _provision(rig)
    _set_data_dir(project_dir, "state/data")
    for base in (rig["data_root"], rig["data_root"] / "config"):
        _write_latch(base / "state" / "data")

    assert _launch_runtime_section(project_dir)["data_dir"] == str(rig["data_root"] / "config" / "state" / "data")


def test_directories_a_runtime_only_started_in_are_not_data(rig: dict) -> None:
    # The runtime creates runs/ at every start, empty until a run is opened.
    project_dir = _provision(rig)
    _set_data_dir(project_dir, "state/data")
    beside = rig["data_root"] / "config" / "state" / "data"
    (beside / "estop").mkdir(parents=True)
    (beside / "runs").mkdir()
    (beside / "runs" / "index.jsonl").write_text("", encoding="utf-8")
    _write_latch(rig["data_root"] / "state" / "data")

    assert _launch_runtime_section(project_dir)["data_dir"] == str(rig["data_root"] / "state" / "data")


def test_a_run_journal_alone_is_data(rig: dict) -> None:
    project_dir = _provision(rig)
    _set_data_dir(project_dir, "state/data")
    runs = rig["data_root"] / "state" / "data" / "runs"
    runs.mkdir(parents=True)
    (runs / "index.jsonl").write_text('{"run_id":"r1"}\n', encoding="utf-8")

    assert _launch_runtime_section(project_dir)["data_dir"] == str(rig["data_root"] / "state" / "data")


def test_an_absolute_data_dir_is_kept(rig: dict, tmp_path: pathlib.Path) -> None:
    project_dir = _provision(rig)
    _set_data_dir(project_dir, str(tmp_path / "elsewhere"))

    assert _launch_runtime_section(project_dir)["data_dir"] == str(tmp_path / "elsewhere")


def test_an_empty_runtime_block_gets_the_data_dir(rig: dict) -> None:
    project_dir = _provision(rig)
    variant = project_dir / canonical.variant_relpath(canonical.MANUAL_VARIANT)
    doc = yaml.safe_load(variant.read_text(encoding="utf-8"))
    doc["runtime"] = None
    variant.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    assert _launch_runtime_section(project_dir) == {"data_dir": str(rig["data_root"] / "anolis-data")}


def test_the_canonical_variant_is_not_given_the_data_dir(rig: dict) -> None:
    # What deploys is the canonical variant; install.sh names the data dir there.
    project_dir = _provision(rig)
    _launch_runtime_section(project_dir)

    variant = yaml.safe_load(
        (project_dir / canonical.variant_relpath(canonical.MANUAL_VARIANT)).read_text(encoding="utf-8")
    )
    assert "data_dir" not in (variant.get("runtime") or {})


def test_a_reprovision_keeps_the_latch_and_the_run_journal(rig: dict) -> None:
    project_dir = _provision(rig)
    data_dir = pathlib.Path(_launch_runtime_section(project_dir)["data_dir"])
    latch = data_dir / "estop" / "0123456789abcdef.json"
    journal = data_dir / "runs" / "index.jsonl"
    for path, body in ((latch, '{"latched":true}\n'), (journal, '{"run_id":"mixed-rig-1"}\n')):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    assert _provision(rig, force=True) == project_dir  # replaces the project directory

    assert latch.read_text(encoding="utf-8") == '{"latched":true}\n'
    assert journal.read_text(encoding="utf-8") == '{"run_id":"mixed-rig-1"}\n'
    assert pathlib.Path(_launch_runtime_section(project_dir)["data_dir"]) == data_dir


@pytest.mark.skipif(sys.platform == "win32", reason="stands in a /bin/sh stub for the runtime")
def test_a_restart_rewrites_a_launch_file_from_before_the_pin(rig: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    # A workbench upgraded to this release restarts a runtime the old one
    # launched: the launch file on disk has no data_dir.
    project_dir = _provision(rig)
    target = launcher.materialize_launch_config(project_dir, canonical.read_project(project_dir))
    old = yaml.safe_load(target.read_text(encoding="utf-8"))
    del old["runtime"]["data_dir"]
    target.write_text(yaml.safe_dump(old, sort_keys=False), encoding="utf-8")

    runtime_bin = rig["prefix"] / "bin" / "anolis-runtime"
    runtime_bin.parent.mkdir(parents=True, exist_ok=True)
    runtime_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    runtime_bin.chmod(0o755)
    spawned: list[Any] = []

    def _popen(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        spawned.append(cmd)
        proc = subprocess.CompletedProcess(cmd, 0, b"", b"")
        proc.pid = 4242  # type: ignore[attr-defined]
        return proc

    monkeypatch.setattr(
        launcher, "_current_runtime_snapshot", lambda clean_stale=False: {"running": True, "project": "mixed-rig"}
    )
    monkeypatch.setattr(launcher, "stop", lambda: None)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(launcher, "_state", dict(launcher._state, log_lines_by_project={}))
    monkeypatch.setattr(launcher.subprocess, "Popen", _popen)

    launcher.restart("mixed-rig", project_dir)

    assert spawned and spawned[0][-1] == str(target)
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["runtime"]["data_dir"] == str(
        rig["data_root"] / "anolis-data"
    )


def test_a_restart_that_cannot_render_leaves_the_runtime_running(rig: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = _provision(rig)
    stopped: list[bool] = []

    def _boom(project_dir: pathlib.Path, document: dict) -> pathlib.Path:
        raise RuntimeError("No host executable configured for provider 'bread0'")

    monkeypatch.setattr(
        launcher, "_current_runtime_snapshot", lambda clean_stale=False: {"running": True, "project": "mixed-rig"}
    )
    monkeypatch.setattr(launcher, "materialize_launch_config", _boom)
    monkeypatch.setattr(launcher, "stop", lambda: stopped.append(True))

    with pytest.raises(RuntimeError, match="No host executable"):
        launcher.restart("mixed-rig", project_dir)
    assert stopped == []
