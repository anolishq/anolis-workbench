"""Unit tests for install.sh deployment delegation."""

from __future__ import annotations

import pathlib
from typing import Callable

import pytest
import requests
import yaml

from anolis_workbench.core import canonical, deploy, releases
from anolis_workbench.core.executor import Executor, RunResult


@pytest.fixture(autouse=True)
def _stub_release_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed the release cache and block network so tests never hit GitHub.

    Since #255 nothing on the deploy path resolves a version — pins are read
    from the project's own machine-profile — so this exists for `run_rollback`,
    which legitimately needs the latest install.sh, and as a tripwire proving
    the rest of the path stays offline.
    """
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {"anolishq/anolis": "0.1.27"})

    def _no_network(*args: object, **kwargs: object) -> None:
        raise requests.RequestException("network disabled in tests")

    monkeypatch.setattr(releases.requests, "get", _no_network)
    monkeypatch.setattr(deploy.requests, "get", _no_network)


@pytest.fixture()
def project_dir(canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path) -> pathlib.Path:
    """An authored canonical project — the only thing deploy accepts now."""
    return canonical_project(
        tmp_path / "workspace",
        machine_id="deploy-fixture",
        behavior="behaviors/local.xml",
    )


class RecordingExecutor(Executor):
    """Fake Executor that records calls and succeeds."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.commands: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.mkdirs: list[str] = []

    def run(self, cmd, *, input=None, sudo=False, timeout=None):
        self.commands.append({"cmd": list(cmd), "sudo": sudo, "timeout": timeout})
        return RunResult(returncode=self.returncode, stdout="ok", stderr="")

    def write_file(self, path, data):
        self.files[path] = data

    def mkdir(self, path):
        self.mkdirs.append(path)

    def file_exists(self, path):
        return False


# ---------------------------------------------------------------------------
# materialize_project_dir
# ---------------------------------------------------------------------------


def test_materialize_produces_install_sh_layout(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    mat = deploy.materialize_project_dir(project_dir, tmp_path / "out")
    pd = mat.project_dir
    # Keyed on machine_id, which is what install.sh's ../anolis-projects/
    # path rewrites resolve against — NOT the (renamable) workbench name.
    assert pd.name == "deploy-fixture"
    assert (pd / "machine-profile.yaml").is_file()
    assert (pd / "config" / "anolis-runtime.manual.yaml").is_file()
    assert (pd / "config" / "anolis-runtime.automation.yaml").is_file()
    assert (pd / "config" / "provider-sim.sim0.yaml").is_file()
    assert (pd / "behaviors" / "local.xml").is_file()
    assert mat.runtime_version == "0.1.27"
    assert mat.provider_kinds == {"sim0": "sim"}


def test_materialize_carries_deploy_tokens_untouched(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """The workbench does NOT rewrite paths to production ones — install.sh
    owns that rewrite. Materialize is an honest copy, so what ships is exactly
    what was authored, tokens and all."""
    mat = deploy.materialize_project_dir(project_dir, tmp_path / "out")
    runtime = yaml.safe_load((mat.project_dir / "config" / "anolis-runtime.manual.yaml").read_text())
    entry = runtime["providers"][0]
    assert entry["command"] == canonical.provider_command_token("sim")
    assert entry["args"] == [
        "--config",
        "../anolis-projects/projects/deploy-fixture/config/provider-sim.sim0.yaml",
    ]
    assert canonical.command_kind(entry["command"]) == "sim"
    # bind is left as authored — install.sh owns the LAN-exposure rewrite, and
    # it only fires on an UNQUOTED 127.0.0.1.
    assert runtime["http"]["bind"] == "127.0.0.1"
    manual_text = (mat.project_dir / "config" / "anolis-runtime.manual.yaml").read_text()
    assert "bind: 127.0.0.1\n" in manual_text


def test_materialize_keeps_automation_out_of_the_manual_variant(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """install.sh REFUSES a non-inert manual variant, so automation lives in
    its own variant. (Before #255 the composer wrote it into the only config
    and every automation deploy was rejected at the target.)"""
    mat = deploy.materialize_project_dir(project_dir, tmp_path / "out")
    manual = yaml.safe_load((mat.project_dir / "config" / "anolis-runtime.manual.yaml").read_text())
    automation = yaml.safe_load((mat.project_dir / "config" / "anolis-runtime.automation.yaml").read_text())

    assert canonical.inertness_violation(manual) is None
    assert automation["automation"]["enabled"] is True
    expected = "../anolis-projects/projects/deploy-fixture/behaviors/local.xml"
    assert automation["automation"]["behavior_tree"] == expected


def test_materialize_uses_the_projects_own_pins_and_never_the_network(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins are AUTHORED data. The old path resolved them from live GitHub
    lookups at deploy time, which could bump the runtime under a running rig
    and made deploying impossible offline."""
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("materialize must not resolve versions from the network")

    monkeypatch.setattr(releases, "latest_release_version", _boom)

    mat = deploy.materialize_project_dir(project_dir, tmp_path / "out")
    profile = yaml.safe_load((mat.project_dir / "machine-profile.yaml").read_text())
    assert profile["components"]["runtime"] == {"repo": "anolishq/anolis", "version": "0.1.27"}
    assert profile["components"]["providers"]["sim"] == {
        "repo": "anolishq/anolis-provider-sim",
        "version": "0.2.5",
    }
    assert mat.runtime_version == "0.1.27"


def test_materialize_fails_on_missing_behavior_file(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    (project_dir / "behaviors" / "local.xml").unlink()
    with pytest.raises(deploy.DeployError, match="missing files"):
        deploy.materialize_project_dir(project_dir, tmp_path / "out")


def test_materialize_fails_without_pins(canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path) -> None:
    """A migrated project has no pins yet — deploy must refuse it rather than
    invent them (#255 decision 1)."""
    unpinned = canonical_project(tmp_path / "unpinned", machine_id="unpinned", pin_components=False)
    with pytest.raises(deploy.DeployError, match="components"):
        deploy.materialize_project_dir(unpinned, tmp_path / "out")


# ---------------------------------------------------------------------------
# fetch_install_sh
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


def test_fetch_install_sh_downloads_pinned_release(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def _get(url, **kwargs):
        urls.append(url)
        return _FakeResponse(200, b"#!/usr/bin/env bash\n")

    monkeypatch.setattr(deploy.requests, "get", _get)
    path = deploy.fetch_install_sh("0.1.27", tmp_path)
    assert path.read_bytes().startswith(b"#!")
    assert urls == ["https://github.com/anolishq/anolis/releases/download/v0.1.27/install.sh"]


def test_fetch_install_sh_raises_on_http_error(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy.requests, "get", lambda *a, **k: _FakeResponse(404))
    with pytest.raises(deploy.DeployError, match="HTTP 404"):
        deploy.fetch_install_sh("0.1.27", tmp_path)


# ---------------------------------------------------------------------------
# deploy_local / deploy_remote
# ---------------------------------------------------------------------------


def _stub_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_fetch(version: str, dest: pathlib.Path) -> pathlib.Path:
        path = dest / "install.sh"
        path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        return path

    monkeypatch.setattr(deploy, "fetch_install_sh", _fake_fetch)


def test_deploy_local_runs_install_sh_project(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    result = deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        no_start=True,
        executor=executor,
    )
    assert result.runtime_version == "0.1.27"
    assert len(executor.commands) == 1
    call = executor.commands[0]
    assert call["sudo"] is True
    assert call["timeout"] == deploy.INSTALL_TIMEOUT_S
    cmd = call["cmd"]
    assert cmd[0] == "bash"
    assert cmd[1].endswith("/install.sh")
    assert cmd[2] == "--project"
    assert cmd[3].endswith("/deploy-fixture")
    assert "--no-start" in cmd
    assert "--prefix" not in cmd  # default prefix omitted


def test_deploy_local_passes_custom_prefix(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        prefix=pathlib.Path("/srv/anolis"),
        executor=executor,
    )
    cmd = executor.commands[0]["cmd"]
    assert "--prefix" in cmd
    assert cmd[cmd.index("--prefix") + 1] == "/srv/anolis"


def test_deploy_local_threads_with_telemetry_export(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Telemetry-export provisioning is delegated to install.sh (anolishq/anolis#137);
    # workbench requests it via the flag, and only when asked.
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        with_telemetry_export=True,
        executor=executor,
    )
    assert "--with-telemetry-export" in executor.commands[0]["cmd"]


def test_deploy_local_omits_telemetry_flag_by_default(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        executor=executor,
    )
    assert "--with-telemetry-export" not in executor.commands[0]["cmd"]


def test_deploy_local_raises_on_install_failure(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor(returncode=1)
    with pytest.raises(deploy.DeployError, match="install.sh failed"):
        deploy.deploy_local(
            project_dir=project_dir,
            project_name="deploy-fixture",
            executor=executor,
        )


def test_deploy_remote_pushes_config_and_runs(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    result = deploy.deploy_remote(
        executor=executor,
        project_dir=project_dir,
        project_name="deploy-fixture",
    )
    assert result.runtime_version == "0.1.27"
    pushed = set(executor.files)
    assert "/tmp/anolis-deploy/deploy-fixture/machine-profile.yaml" in pushed
    assert "/tmp/anolis-deploy/deploy-fixture/config/anolis-runtime.manual.yaml" in pushed
    assert "/tmp/anolis-deploy/deploy-fixture/config/provider-sim.sim0.yaml" in pushed
    assert "/tmp/anolis-deploy/deploy-fixture/behaviors/local.xml" in pushed
    assert "/tmp/anolis-deploy/install.sh" in pushed
    call = executor.commands[-1]
    assert call["sudo"] is True
    assert call["cmd"][:4] == [
        "bash",
        "/tmp/anolis-deploy/install.sh",
        "--project",
        "/tmp/anolis-deploy/deploy-fixture",
    ]


# ---------------------------------------------------------------------------
# run_rollback
# ---------------------------------------------------------------------------


def test_run_rollback_stages_and_invokes_install_sh(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    output = deploy.run_rollback(executor)
    assert output == "ok"
    assert "/tmp/anolis-deploy/install.sh" in executor.files
    call = executor.commands[-1]
    assert call["sudo"] is True
    assert call["cmd"] == ["bash", "/tmp/anolis-deploy/install.sh", "--rollback"]


def test_run_rollback_passes_custom_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.run_rollback(executor, prefix=pathlib.Path("/srv/anolis"))
    cmd = executor.commands[-1]["cmd"]
    assert cmd[cmd.index("--prefix") + 1] == "/srv/anolis"


def test_run_rollback_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor(returncode=1)
    with pytest.raises(deploy.DeployError, match="--rollback failed"):
        deploy.run_rollback(executor)


def test_run_rollback_raises_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {"anolishq/anolis": None})
    with pytest.raises(deploy.DeployError, match="latest anolis release"):
        deploy.run_rollback(RecordingExecutor())


# ---------------------------------------------------------------------------
# stage_bundle
# ---------------------------------------------------------------------------


def test_stage_bundle_invokes_stage_and_returns_tarball(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    out_dir = tmp_path / "bundles"
    recorded: list[list[str]] = []

    class _StagingExecutor(RecordingExecutor):
        def run(self, cmd, *, input=None, sudo=False, timeout=None):
            recorded.append(list(cmd))
            (out_dir / "anolis-deploy-fixture-0.1.27-arm64.tar.gz").write_bytes(b"tar")
            return RunResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(deploy, "LocalExecutor", _StagingExecutor)
    tarball = deploy.stage_bundle(
        project_dir=project_dir,
        project_name="deploy-fixture",
        out_dir=out_dir,
        arch="arm64",
    )
    assert tarball.name == "anolis-deploy-fixture-0.1.27-arm64.tar.gz"
    cmd = recorded[0]
    assert cmd[0] == "bash"
    assert "--stage" in cmd and "--project" in cmd
    assert cmd[cmd.index("--arch") + 1] == "arm64"


def test_stage_bundle_raises_when_no_tarball(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)

    class _NoopExecutor(RecordingExecutor):
        pass

    monkeypatch.setattr(deploy, "LocalExecutor", _NoopExecutor)
    with pytest.raises(deploy.DeployError, match="produced no bundle"):
        deploy.stage_bundle(
            project_dir=project_dir,
            project_name="deploy-fixture",
            out_dir=tmp_path / "bundles",
        )


def test_materialize_refuses_when_tokens_name_another_project(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """install.sh rewrites `../anolis-projects/projects/<X>/` using the TOKEN's
    own <X>, but installs under the directory it is handed. When they disagree
    the install SUCCEEDS and the rig is broken — configs resolve to a path
    nothing was written to — so it has to be caught here."""
    variant = project_dir / canonical.variant_relpath(canonical.MANUAL_VARIANT)
    variant.write_text(
        variant.read_text(encoding="utf-8").replace("/projects/deploy-fixture/", "/projects/some-other-rig/"),
        encoding="utf-8",
    )
    with pytest.raises(deploy.DeployError, match="some-other-rig"):
        deploy.materialize_project_dir(project_dir, tmp_path / "out")


def test_stage_bundle_does_not_pass_variant_to_install_sh(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install.sh's bundle assembly always stages `manual` and ignores the flag.
    Passing it would tell the operator they had chosen something they had not."""
    _stub_fetch(monkeypatch)
    out_dir = tmp_path / "bundles"
    recorded: list[list[str]] = []

    class _StagingExecutor(RecordingExecutor):
        def run(self, cmd, *, input=None, sudo=False, timeout=None):
            recorded.append(list(cmd))
            (out_dir / "anolis-deploy-fixture-0.1.27-arm64.tar.gz").write_bytes(b"tar")
            return RunResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(deploy, "LocalExecutor", _StagingExecutor)
    deploy.stage_bundle(
        project_dir=project_dir,
        project_name="deploy-fixture",
        out_dir=out_dir,
        arch="arm64",
        variant="automation",
    )
    assert "--variant" not in recorded[0]
