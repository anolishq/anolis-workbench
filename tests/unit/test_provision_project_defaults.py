"""Which project install, remote and bundle act on (#668).

The README's token-rotation step ran `anolis-provision install
--workbench-service`, which seeded sim-quickstart beside the machine's real
project and redeployed the runtime from it; a mistyped --project was seeded
and deployed as a new simulator project. Seeding a project from a template now
takes a --template, or an empty workspace, and refreshing the workbench service
is its own command.

The workspace is a temp SYSTEMS_ROOT holding one project, bioreactor. The
deploys and the workbench service install are stubs that record their calls.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import pytest

from anolis_workbench.cli import provision_cli
from anolis_workbench.core import deploy, installer, workbench_service
from anolis_workbench.core import executor as executor_module


class _FakeSSH:
    def __init__(self, **kwargs: object) -> None:
        pass

    def run(self, cmd: list[str], **kwargs: object) -> executor_module.RunResult:
        return executor_module.RunResult(returncode=0, stdout="x86_64\n", stderr="")


def _result(name: str, prefix: pathlib.Path) -> deploy.DeployResult:
    return deploy.DeployResult(project_name=name, runtime_version="0.3.0", prefix=str(prefix), output="")


@pytest.fixture
def ws(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    systems = tmp_path / "systems"
    systems.mkdir()
    monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems)
    monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems)
    installer.provision_project("sim-quickstart", "bioreactor", tmp_path / "prefix", systems_root=systems)

    calls: dict[str, list[dict[str, Any]]] = {
        "deploy_local": [],
        "deploy_remote": [],
        "stage_bundle": [],
        "service": [],
    }

    def _deploy_local(**kwargs: Any) -> deploy.DeployResult:
        calls["deploy_local"].append(kwargs)
        return _result(str(kwargs["project_name"]), tmp_path / "prefix")

    def _deploy_remote(**kwargs: Any) -> deploy.DeployResult:
        calls["deploy_remote"].append(kwargs)
        return _result(str(kwargs["project_name"]), tmp_path / "prefix")

    def _stage_bundle(**kwargs: Any) -> pathlib.Path:
        calls["stage_bundle"].append(kwargs)
        return tmp_path / "out" / "bundle.tar.gz"

    def _install_service(**kwargs: Any) -> workbench_service.WorkbenchServiceResult:
        calls["service"].append(kwargs)
        return workbench_service.WorkbenchServiceResult(installed=True, enabled=True, started=True)

    monkeypatch.setattr(deploy, "deploy_local", _deploy_local)
    monkeypatch.setattr(deploy, "deploy_remote", _deploy_remote)
    monkeypatch.setattr(deploy, "stage_bundle", _stage_bundle)
    monkeypatch.setattr(workbench_service, "install_service", _install_service)
    monkeypatch.setattr(workbench_service, "invoking_user", lambda: "pi")
    monkeypatch.setattr(executor_module, "SubprocessSSHExecutor", _FakeSSH)
    return {"systems": systems, "calls": calls, "tmp": tmp_path}


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["anolis-provision", *argv])
    return provision_cli.main()


def _workspace(ws: dict[str, Any]) -> list[str]:
    return sorted(p.name for p in ws["systems"].iterdir())


def test_the_token_refresh_command_touches_neither_the_workspace_nor_the_runtime(
    ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(monkeypatch, "workbench-service") == 0

    assert len(ws["calls"]["service"]) == 1
    assert ws["calls"]["deploy_local"] == []
    assert _workspace(ws) == ["bioreactor"]


def test_install_with_no_project_next_to_another_project_fails_naming_it(
    ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The README's old rotation step, on a machine whose project is bioreactor.
    assert _run(monkeypatch, "install", "--workbench-service") == 1

    err = capsys.readouterr().err
    assert "bioreactor" in err
    assert "--template sim-quickstart" in err
    assert ws["calls"]["deploy_local"] == []
    assert ws["calls"]["service"] == []
    assert _workspace(ws) == ["bioreactor"]


@pytest.mark.parametrize(
    "argv",
    [
        ["install", "--project", "bioreacter"],
        ["bundle", "--project", "bioreacter", "--arch", "x86_64", "--out", "OUT"],
        ["remote", "--target", "pi@host", "--project", "bioreacter"],
    ],
    ids=["install", "bundle", "remote"],
)
def test_a_project_not_in_the_workspace_fails_naming_the_ones_that_are(
    ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    argv = [str(ws["tmp"] / "out") if a == "OUT" else a for a in argv]
    assert _run(monkeypatch, *argv) == 1

    err = capsys.readouterr().err
    assert "bioreacter" in err
    assert "bioreactor" in err
    assert all(not c for c in ws["calls"].values())
    assert _workspace(ws) == ["bioreactor"]


def test_an_explicit_template_still_seeds_a_new_project(ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, "install", "--template", "sim-quickstart", "--project", "sim2") == 0

    assert _workspace(ws) == ["bioreactor", "sim2"]
    assert [c["project_name"] for c in ws["calls"]["deploy_local"]] == ["sim2"]


def test_an_existing_project_is_used_as_it_is(ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, "install", "--project", "bioreactor") == 0

    assert _workspace(ws) == ["bioreactor"]
    assert [c["project_name"] for c in ws["calls"]["deploy_local"]] == ["bioreactor"]


def test_an_empty_workspace_still_gets_sim_quickstart(ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    # A fresh machine's `anolis-provision install --with-observability` (README).
    import shutil

    shutil.rmtree(ws["systems"] / "bioreactor")
    assert _run(monkeypatch, "install", "--with-observability") == 0

    assert _workspace(ws) == ["sim-quickstart"]
    assert [c["project_name"] for c in ws["calls"]["deploy_local"]] == ["sim-quickstart"]


def test_a_rerun_on_a_simulator_only_workspace_reuses_sim_quickstart(
    ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The quickstart's install run again: its own project is not "another" one.
    import shutil

    shutil.rmtree(ws["systems"] / "bioreactor")
    installer.provision_project("sim-quickstart", "sim-quickstart", ws["tmp"] / "prefix", systems_root=ws["systems"])
    assert _run(monkeypatch, "install") == 0

    assert _workspace(ws) == ["sim-quickstart"]
    assert [c["project_name"] for c in ws["calls"]["deploy_local"]] == ["sim-quickstart"]
