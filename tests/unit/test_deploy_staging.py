"""Where remote deploy and rollback stage what sudo then runs (#652, #655).

Both used a fixed /tmp/anolis-deploy: another local account on the target could
create it first and swap install.sh before the sudo run (#652), and files
removed from a project since the last deploy stayed there for install.sh's
globs to pick up (#655). Each run now stages into a fresh private directory and
removes it afterwards.

TreeExecutor stands in for the target: every path it is given lives under the
test's temp dir, mktemp makes a unique directory there, and rm only ever acts
inside that tree. The sudo bash call snapshots the staged files instead of
running anything. No test reaches the host.
"""

from __future__ import annotations

import pathlib
import shutil
import tempfile
from typing import Callable

import pytest

from anolis_workbench.core import deploy
from anolis_workbench.core.executor import Executor, RunResult

FIXED = "/tmp/anolis-deploy"


class TreeExecutor(Executor):
    def __init__(self, root: pathlib.Path, *, mktemp_fails: bool = False, install_rc: int = 0) -> None:
        self.root = root
        (root / "tmp").mkdir(parents=True, exist_ok=True)
        self.mktemp_fails = mktemp_fails
        self.install_rc = install_rc
        self.commands: list[list[str]] = []
        self.sudo_runs: list[dict[str, object]] = []

    def local(self, path: str) -> pathlib.Path:
        assert path.startswith("/"), path
        return self.root / path.lstrip("/")

    def run(self, cmd, *, input=None, sudo=False, timeout=None):  # type: ignore[no-untyped-def]
        cmd = list(cmd)
        self.commands.append(cmd)
        if cmd[0] == "mktemp":
            if self.mktemp_fails:
                return RunResult(returncode=1, stdout="", stderr="mktemp: failed")
            made = tempfile.mkdtemp(prefix="anolis-deploy.", dir=self.root / "tmp")
            return RunResult(returncode=0, stdout="/tmp/" + pathlib.Path(made).name + "\n", stderr="")
        if cmd[0] == "test" and cmd[1] == "-L":
            return RunResult(returncode=0 if self.local(cmd[-1]).is_symlink() else 1, stdout="", stderr="")
        if cmd[0] == "rm":
            target = self.local(cmd[-1])
            assert target.resolve().is_relative_to(self.root.resolve()), target
            shutil.rmtree(target, ignore_errors=True)
            return RunResult(returncode=0, stdout="", stderr="")
        if cmd[0] == "bash" and sudo:
            project = cmd[cmd.index("--project") + 1] if "--project" in cmd else None
            staged = (
                sorted(
                    p.relative_to(self.local(project)).as_posix() for p in self.local(project).rglob("*") if p.is_file()
                )
                if project
                else []
            )
            self.sudo_runs.append({"cmd": cmd, "staged": staged})
            return RunResult(
                returncode=self.install_rc, stdout="ok", stderr="install failed" if self.install_rc else ""
            )
        return RunResult(returncode=1, stdout="", stderr="")

    def write_file(self, path, data):  # type: ignore[no-untyped-def]
        self.local(path).write_bytes(data)

    def mkdir(self, path):  # type: ignore[no-untyped-def]
        self.local(path).mkdir(parents=True, exist_ok=True)

    def file_exists(self, path):  # type: ignore[no-untyped-def]
        return self.local(path).exists()


@pytest.fixture()
def project_dir(canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path) -> pathlib.Path:
    return canonical_project(tmp_path / "workspace", machine_id="deploy-fixture", behavior="behaviors/local.xml")


@pytest.fixture(autouse=True)
def _stub_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_fetch(version: str, dest: pathlib.Path) -> tuple[pathlib.Path, str | None]:
        path = dest / "install.sh"
        path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        return path, None

    monkeypatch.setattr(deploy, "fetch_install_sh", _fake_fetch)
    monkeypatch.setattr(deploy.releases, "latest_release_version", lambda repo: "0.3.0")


def _staging_of(run: dict[str, object]) -> str:
    cmd = run["cmd"]
    assert isinstance(cmd, list)
    return str(cmd[1]).rsplit("/", 1)[0]


def _deploy(executor: TreeExecutor, project_dir: pathlib.Path) -> None:
    deploy.deploy_remote(executor=executor, project_dir=project_dir, project_name="deploy-fixture")


def test_a_planted_staging_dir_is_not_used(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    executor = TreeExecutor(tmp_path / "target")
    planted = executor.local(FIXED)
    planted.mkdir(mode=0o777)
    (planted / "install.sh").write_text("echo planted\n", encoding="utf-8")

    _deploy(executor, project_dir)

    (run,) = executor.sudo_runs
    staging = _staging_of(run)
    assert staging != FIXED
    assert pathlib.PurePosixPath(staging).name.startswith("anolis-deploy.")
    assert (planted / "install.sh").read_text(encoding="utf-8") == "echo planted\n"


def test_a_file_removed_from_the_project_is_not_staged_again(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    executor = TreeExecutor(tmp_path / "target")
    old = project_dir / "config" / "provider-old.yaml"
    old.write_text("provider: {}\n", encoding="utf-8")
    _deploy(executor, project_dir)
    assert "config/provider-old.yaml" in executor.sudo_runs[0]["staged"]  # type: ignore[operator]

    old.unlink()
    _deploy(executor, project_dir)

    assert "config/provider-old.yaml" not in executor.sudo_runs[1]["staged"]  # type: ignore[operator]


@pytest.mark.parametrize("install_rc", [0, 1], ids=["success", "failure"])
def test_the_staging_dir_is_removed_after_the_run(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, install_rc: int
) -> None:
    executor = TreeExecutor(tmp_path / "target", install_rc=install_rc)
    if install_rc:
        with pytest.raises(deploy.DeployError):
            _deploy(executor, project_dir)
    else:
        _deploy(executor, project_dir)

    staging = _staging_of(executor.sudo_runs[0])
    assert not executor.local(staging).exists()
    assert not executor.local(FIXED).exists()


def test_no_private_staging_dir_stops_the_deploy_before_sudo(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    executor = TreeExecutor(tmp_path / "target", mktemp_fails=True)
    with pytest.raises(deploy.DeployError, match="staging"):
        _deploy(executor, project_dir)
    assert executor.sudo_runs == []


def test_rollback_stages_privately_and_cleans_up(tmp_path: pathlib.Path) -> None:
    executor = TreeExecutor(tmp_path / "target")
    executor.local(FIXED).mkdir(mode=0o777)

    deploy.run_rollback(executor)

    (run,) = executor.sudo_runs
    staging = _staging_of(run)
    assert staging != FIXED
    assert run["cmd"] == ["bash", f"{staging}/install.sh", "--rollback"]
    assert not executor.local(staging).exists()


def test_rollback_without_a_private_staging_dir_stops_before_sudo(tmp_path: pathlib.Path) -> None:
    executor = TreeExecutor(tmp_path / "target", mktemp_fails=True)
    with pytest.raises(deploy.DeployError, match="staging"):
        deploy.run_rollback(executor)
    assert executor.sudo_runs == []


@pytest.mark.parametrize(
    "path",
    [
        "",
        "tmp/anolis-deploy.abc",
        "/",
        "/tmp",
        FIXED,
        "/tmp/anolis-deploy.x/../..",
        "/home/pi/anolis-deploy.abcdefghij",
    ],
)
def test_only_a_staging_dir_this_executor_made_can_be_removed(tmp_path: pathlib.Path, path: str) -> None:
    executor = TreeExecutor(tmp_path / "target")
    with pytest.raises(ValueError):
        executor.remove_private_dir(path)
    assert not any(c[0] == "rm" for c in executor.commands)


def test_a_symlinked_staging_dir_is_not_removed(tmp_path: pathlib.Path) -> None:
    executor = TreeExecutor(tmp_path / "target")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (executor.local("/tmp") / "anolis-deploy.abcdefghij").symlink_to(elsewhere)
    with pytest.raises(ValueError):
        executor.remove_private_dir("/tmp/anolis-deploy.abcdefghij")
    assert not any(c[0] == "rm" for c in executor.commands)
    assert elsewhere.is_dir()
