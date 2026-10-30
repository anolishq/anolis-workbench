"""The project install.sh put on this machine, imported at startup (#722).

Operate could reach the runtime install.sh installed only after the user
imported its project by hand, and an import made before an upgrade went
stale (#558). When <prefix>/manifest.json exists, the workbench imports
<prefix>/projects/<profile>/ at startup and re-syncs it on every start; the
proxy reaches the runtime on the port <prefix>/config/runtime.yaml gives.
"""

from __future__ import annotations

import json
import pathlib
import shutil
from typing import Any

import pytest

from anolis_workbench.core import installed_project, launcher, projects
from anolis_workbench.server.routes import operate

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "imported-profile"
PROFILE = "imported-profile"
PORT = 18123


@pytest.fixture
def prefix(tmp_path: pathlib.Path) -> pathlib.Path:
    """An install prefix as install.sh leaves it."""
    root = tmp_path / "opt-anolis"
    shutil.copytree(FIXTURE, root / "projects" / PROFILE)
    (root / "config").mkdir(parents=True)
    (root / "config" / "runtime.yaml").write_text(
        f"runtime:\n  name: rig\nhttp:\n  enabled: true\n  bind: 0.0.0.0\n  port: {PORT}\n", encoding="utf-8"
    )
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "profile": PROFILE, "version": "0.6.0", "prefix": str(root)}),
        encoding="utf-8",
    )
    return root


def _meta(name: str) -> dict[str, Any]:
    meta: dict[str, Any] = projects.get_project(name)["meta"]
    return meta


def test_the_installed_project_is_imported_and_marked(prefix: pathlib.Path) -> None:
    assert installed_project.sync(prefix) == PROFILE

    assert PROFILE in [p["name"] for p in projects.list_projects()]
    assert _meta(PROFILE)["installed_from"] == str(prefix)
    assert not projects.is_authored(PROFILE)


def test_a_restart_resyncs_it_from_the_prefix(prefix: pathlib.Path) -> None:
    installed_project.sync(prefix)
    variant = "config/anolis-runtime.bioreactor.manual.yaml"
    upgraded = (prefix / "projects" / PROFILE / variant).read_text(encoding="utf-8") + "# upgraded\n"
    (prefix / "projects" / PROFILE / variant).write_text(upgraded, encoding="utf-8")

    assert installed_project.sync(prefix) == PROFILE

    assert (projects.project_dir(PROFILE) / variant).read_text(encoding="utf-8") == upgraded


def test_it_cannot_be_edited_in_compose(prefix: pathlib.Path) -> None:
    installed_project.sync(prefix)

    with pytest.raises(projects.ReadOnlyProjectError):
        projects.save_project(PROFILE, projects.get_project(PROFILE))


def test_a_users_own_project_of_that_name_is_kept(
    prefix: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANOLIS_IMPORT_ROOTS", str(tmp_path))
    projects.import_project(str(prefix / "projects" / PROFILE))  # the user's own import
    before = _meta(PROFILE)

    name = installed_project.sync(prefix)

    assert name is not None and name != PROFILE
    assert _meta(PROFILE) == before, "the user's project was overwritten"
    assert _meta(name)["installed_from"] == str(prefix)


@pytest.mark.parametrize("damage", ["manifest", "runtime config", "project"])
def test_nothing_is_imported_when_the_install_cannot_be_read(prefix: pathlib.Path, damage: str) -> None:
    if damage == "manifest":
        (prefix / "manifest.json").write_text("{not json", encoding="utf-8")
    elif damage == "runtime config":
        (prefix / "config" / "runtime.yaml").unlink()
    else:
        shutil.rmtree(prefix / "projects" / PROFILE)

    assert installed_project.sync(prefix) is None
    assert projects.list_projects() == []


def test_no_manifest_means_no_install(prefix: pathlib.Path) -> None:
    (prefix / "manifest.json").unlink()

    assert installed_project.sync(prefix) is None
    assert projects.list_projects() == []


def test_operate_reaches_it_on_the_installed_configs_port(
    prefix: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed_project.sync(prefix)
    probed: list[tuple[str, int]] = []

    def _alive(bind: str, port: int, timeout_s: float = 0.5) -> bool:
        probed.append((bind, port))
        return (bind, port) == ("127.0.0.1", PORT)

    monkeypatch.setattr(launcher, "_runtime_alive", _alive)

    found = launcher._discover_external_runtime()

    assert found is not None and found["project"] == PROFILE and found["external"]
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_URL", raising=False)
    monkeypatch.setattr(launcher, "get_status", lambda: {"running": True, "active_project": PROFILE})
    base, error, _ = operate._runtime_base()
    assert (base, error) == (f"http://127.0.0.1:{PORT}", None)


def test_the_server_syncs_it_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from anolis_workbench.server import app as app_module

    synced: list[bool] = []

    class _Server:
        def __init__(self, *args: Any) -> None:
            pass

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(app_module, "verify_environment", lambda: None)
    monkeypatch.setattr(app_module.projects_module, "cleanup_stale_running_files", lambda: None)
    monkeypatch.setattr(installed_project, "sync", lambda *args: synced.append(True))
    monkeypatch.setattr(app_module, "_WorkbenchServer", _Server)

    app_module.main(host="127.0.0.1", port=0, open_browser=False)

    assert synced == [True]
