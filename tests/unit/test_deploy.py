"""Unit tests for install.sh deployment delegation."""

from __future__ import annotations

import pathlib

import pytest
import requests
import yaml

from anolis_workbench.core import deploy, releases
from anolis_workbench.core.executor import Executor, RunResult


@pytest.fixture(autouse=True)
def _stub_release_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed the release cache and block network so tests never hit GitHub."""
    monkeypatch.setattr(
        releases,
        "_RELEASE_CACHE",
        {"anolishq/anolis": "0.1.27", "anolishq/anolis-provider-sim": "0.2.5"},
    )

    def _no_network(*args: object, **kwargs: object) -> None:
        raise requests.RequestException("network disabled in tests")

    monkeypatch.setattr(releases.requests, "get", _no_network)
    monkeypatch.setattr(deploy.requests, "get", _no_network)


def _make_system() -> dict:
    return {
        "schema_version": 1,
        "meta": {"name": "Deploy Fixture"},
        "topology": {
            "runtime": {
                "name": "anolis-main",
                "http_port": 8080,
                "http_bind": "127.0.0.1",
                "polling_interval_ms": 500,
                "automation_enabled": True,
                "behavior_tree_path": "behaviors/local.xml",
                "providers": [{"id": "sim0", "kind": "sim", "timeout_ms": 5000, "restart_policy": {"enabled": False}}],
            },
            "providers": {
                "sim0": {
                    "kind": "sim",
                    "provider_name": "sim0",
                    "startup_policy": "degraded",
                    "simulation_mode": "non_interacting",
                    "tick_rate_hz": 10.0,
                    "devices": [{"id": "tempctl0", "type": "tempctl", "initial_temp": 25.0}],
                }
            },
        },
        "paths": {
            "runtime_executable": "build/dev-release/core/anolis-runtime",
            "providers": {"sim0": {"executable": "../anolis-provider-sim/build/dev-release/anolis-provider-sim"}},
        },
    }


def _make_workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    ws = tmp_path / "workspace"
    (ws / "behaviors").mkdir(parents=True)
    (ws / "behaviors" / "local.xml").write_text("<root />\n", encoding="utf-8")
    return ws


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


def test_materialize_produces_install_sh_layout(tmp_path: pathlib.Path) -> None:
    ws = _make_workspace(tmp_path)
    mat = deploy.materialize_project_dir(
        system=_make_system(),
        project_name="deploy-fixture",
        workspace_dir=ws,
        dest=tmp_path / "out",
    )
    pd = mat.project_dir
    assert pd.name == "deploy-fixture"
    assert (pd / "machine-profile.yaml").is_file()
    assert (pd / "config" / "anolis-runtime.manual.yaml").is_file()
    assert (pd / "config" / "provider-sim0.yaml").is_file()
    assert (pd / "behaviors" / "local.xml").is_file()
    assert mat.runtime_version == "0.1.27"
    assert mat.provider_kinds == {"sim0": "sim"}


def test_materialize_writes_production_paths(tmp_path: pathlib.Path) -> None:
    ws = _make_workspace(tmp_path)
    mat = deploy.materialize_project_dir(
        system=_make_system(),
        project_name="deploy-fixture",
        workspace_dir=ws,
        dest=tmp_path / "out",
    )
    runtime = yaml.safe_load((mat.project_dir / "config" / "anolis-runtime.manual.yaml").read_text())
    entry = runtime["providers"][0]
    assert entry["command"] == "/opt/anolis/bin/anolis-provider-sim"
    assert entry["args"] == ["--config", "/opt/anolis/config/providers/sim0.yaml"]
    assert runtime["automation"]["behavior_tree"] == "/opt/anolis/projects/deploy-fixture/behaviors/local.xml"
    # bind is left as authored — install.sh owns the bind rewrite
    assert runtime["http"]["bind"] == "127.0.0.1"


def test_materialize_machine_profile_pins_components(tmp_path: pathlib.Path) -> None:
    ws = _make_workspace(tmp_path)
    mat = deploy.materialize_project_dir(
        system=_make_system(),
        project_name="deploy-fixture",
        workspace_dir=ws,
        dest=tmp_path / "out",
    )
    profile = yaml.safe_load((mat.project_dir / "machine-profile.yaml").read_text())
    assert profile["components"]["runtime"] == {"repo": "anolishq/anolis", "version": "0.1.27"}
    assert profile["components"]["providers"]["sim"] == {
        "repo": "anolishq/anolis-provider-sim",
        "version": "0.2.5",
    }
    assert profile["runtime_profiles"]["manual"] == "config/anolis-runtime.manual.yaml"
    assert profile["providers"]["sim0"]["config"] == "config/provider-sim0.yaml"
    assert profile["behaviors"] == ["behaviors/local.xml"]


def test_materialize_fails_without_kind(tmp_path: pathlib.Path) -> None:
    system = _make_system()
    # Provider declared in the runtime but absent from the topology → no kind.
    del system["topology"]["providers"]["sim0"]
    with pytest.raises(deploy.DeployError, match="no kind"):
        deploy.materialize_project_dir(
            system=system,
            project_name="deploy-fixture",
            workspace_dir=_make_workspace(tmp_path),
            dest=tmp_path / "out",
        )


def test_materialize_fails_offline(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        releases,
        "_RELEASE_CACHE",
        {"anolishq/anolis": None, "anolishq/anolis-provider-sim": None},
    )
    with pytest.raises(deploy.DeployError, match="released component versions"):
        deploy.materialize_project_dir(
            system=_make_system(),
            project_name="deploy-fixture",
            workspace_dir=_make_workspace(tmp_path),
            dest=tmp_path / "out",
        )


def test_materialize_fails_on_missing_behavior_file(tmp_path: pathlib.Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    with pytest.raises(deploy.DeployError, match="Behavior tree file not found"):
        deploy.materialize_project_dir(
            system=_make_system(),
            project_name="deploy-fixture",
            workspace_dir=ws,
            dest=tmp_path / "out",
        )


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


def test_deploy_local_runs_install_sh_project(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    result = deploy.deploy_local(
        system=_make_system(),
        project_name="deploy-fixture",
        workspace_dir=_make_workspace(tmp_path),
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


def test_deploy_local_passes_custom_prefix(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(
        system=_make_system(),
        project_name="deploy-fixture",
        workspace_dir=_make_workspace(tmp_path),
        prefix=pathlib.Path("/srv/anolis"),
        executor=executor,
    )
    cmd = executor.commands[0]["cmd"]
    assert "--prefix" in cmd
    assert cmd[cmd.index("--prefix") + 1] == "/srv/anolis"


def test_deploy_local_raises_on_install_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor(returncode=1)
    with pytest.raises(deploy.DeployError, match="install.sh failed"):
        deploy.deploy_local(
            system=_make_system(),
            project_name="deploy-fixture",
            workspace_dir=_make_workspace(tmp_path),
            executor=executor,
        )


def test_deploy_remote_pushes_config_and_runs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    result = deploy.deploy_remote(
        executor=executor,
        system=_make_system(),
        project_name="deploy-fixture",
        workspace_dir=_make_workspace(tmp_path),
    )
    assert result.runtime_version == "0.1.27"
    pushed = set(executor.files)
    assert "/tmp/anolis-deploy/deploy-fixture/machine-profile.yaml" in pushed
    assert "/tmp/anolis-deploy/deploy-fixture/config/anolis-runtime.manual.yaml" in pushed
    assert "/tmp/anolis-deploy/deploy-fixture/config/provider-sim0.yaml" in pushed
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
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
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
        system=_make_system(),
        project_name="deploy-fixture",
        workspace_dir=_make_workspace(tmp_path),
        out_dir=out_dir,
        arch="arm64",
    )
    assert tarball.name == "anolis-deploy-fixture-0.1.27-arm64.tar.gz"
    cmd = recorded[0]
    assert cmd[0] == "bash"
    assert "--stage" in cmd and "--project" in cmd
    assert cmd[cmd.index("--arch") + 1] == "arm64"


def test_stage_bundle_raises_when_no_tarball(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)

    class _NoopExecutor(RecordingExecutor):
        pass

    monkeypatch.setattr(deploy, "LocalExecutor", _NoopExecutor)
    with pytest.raises(deploy.DeployError, match="produced no bundle"):
        deploy.stage_bundle(
            system=_make_system(),
            project_name="deploy-fixture",
            workspace_dir=_make_workspace(tmp_path),
            out_dir=tmp_path / "bundles",
        )
