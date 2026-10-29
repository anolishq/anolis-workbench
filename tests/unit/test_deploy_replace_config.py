"""Replace an installed runtime's config from a deploy (#581).

install.sh keeps an existing runtime.yaml on every install; `--replace-config`
(anolis 0.1.56, anolishq/anolis#470) moves it to runtime.yaml.replaced-<UTC>
and installs the variant instead, and refuses a variant that is not inert. The
deploy passes the flag on, refuses up front what install.sh would refuse, and
reports where the old config went.
"""

from __future__ import annotations

import pathlib
from typing import Any, Callable

import pytest

from anolis_workbench.core import canonical, deploy
from anolis_workbench.core.executor import Executor, RunResult

STAGING = "/tmp/anolis-deploy.AbCdE12345"
REPLACED = (
    "  config: replacing /opt/anolis/config/runtime.yaml; the previous file is at "
    "/opt/anolis/config/runtime.yaml.replaced-20261029T010203Z\n"
)


class InstallExecutor(Executor):
    """Records commands; install.sh, run under sudo, prints `output`."""

    def __init__(self, output: str = "ok") -> None:
        self.output = output
        self.commands: list[list[str]] = []
        self.sudo_runs: list[list[str]] = []

    def make_private_dir(self) -> str:
        return STAGING

    def remove_private_dir(self, path: str) -> None:
        pass

    def run(self, cmd, *, input=None, sudo=False, timeout=None):
        self.commands.append(list(cmd))
        if sudo and cmd[0] == "bash":
            self.sudo_runs.append(list(cmd))
            return RunResult(returncode=0, stdout=self.output, stderr="")
        return RunResult(returncode=1, stdout="", stderr="")

    def write_file(self, path, data):
        pass

    def mkdir(self, path):
        pass

    def file_exists(self, path):
        return False


@pytest.fixture(autouse=True)
def _stub_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_fetch(version: str, dest: pathlib.Path) -> tuple[pathlib.Path, str | None]:
        path = dest / "install.sh"
        path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        return path, None

    monkeypatch.setattr(deploy, "fetch_install_sh", _fake_fetch)


def _project(
    canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path, runtime: str = "0.4.0"
) -> pathlib.Path:
    return canonical_project(
        tmp_path / "workspace",
        behavior="behaviors/local.xml",  # adds the non-inert `automation` variant
        components={
            "runtime": {"repo": "anolishq/anolis", "version": runtime},
            "providers": {"sim": {"repo": "anolishq/anolis-provider-sim", "version": "0.2.8"}},
        },
    )


def _deploy(target: str, executor: InstallExecutor, project_dir: pathlib.Path, **kwargs: Any) -> deploy.DeployResult:
    if target == "remote":
        return deploy.deploy_remote(executor=executor, project_dir=project_dir, project_name="rig", **kwargs)
    return deploy.deploy_local(executor=executor, project_dir=project_dir, project_name="rig", **kwargs)


@pytest.mark.parametrize("target", ["remote", "local"])
def test_replace_config_reaches_install_sh(
    canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path, target: str
) -> None:
    executor = InstallExecutor(output=REPLACED)
    result = _deploy(target, executor, _project(canonical_project, tmp_path), replace_config=True)

    (cmd,) = executor.sudo_runs
    assert "--replace-config" in cmd
    assert result.replaced_config == (
        "/opt/anolis/config/runtime.yaml",
        "/opt/anolis/config/runtime.yaml.replaced-20261029T010203Z",
    )


@pytest.mark.parametrize("target", ["remote", "local"])
def test_without_replace_config_the_flag_is_not_sent(
    canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path, target: str
) -> None:
    executor = InstallExecutor()
    result = _deploy(target, executor, _project(canonical_project, tmp_path))

    (cmd,) = executor.sudo_runs
    assert "--replace-config" not in cmd
    assert result.replaced_config is None


@pytest.mark.parametrize("target", ["remote", "local"])
def test_a_variant_that_is_not_inert_is_refused_before_install_sh(
    canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path, target: str
) -> None:
    executor = InstallExecutor()
    with pytest.raises(deploy.DeployError, match="inert"):
        _deploy(
            target,
            executor,
            _project(canonical_project, tmp_path),
            replace_config=True,
            variant=canonical.AUTOMATION_VARIANT,
        )
    assert executor.sudo_runs == []


@pytest.mark.parametrize("target", ["remote", "local"])
def test_a_runtime_pin_before_replace_config_is_refused_before_install_sh(
    canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path, target: str
) -> None:
    executor = InstallExecutor()
    with pytest.raises(deploy.DeployError, match="0.1.56"):
        _deploy(target, executor, _project(canonical_project, tmp_path, runtime="0.1.55"), replace_config=True)
    assert executor.sudo_runs == []


def test_replaced_config_reads_install_sh_s_backup_line() -> None:
    assert deploy.replaced_config("✓ verify: all checksums pass\n" + REPLACED + "✓ health: ok\n") == (
        "/opt/anolis/config/runtime.yaml",
        "/opt/anolis/config/runtime.yaml.replaced-20261029T010203Z",
    )
    assert deploy.replaced_config("→ config (runtime): /opt/anolis/config/runtime.yaml exists (preserved)\n") is None
