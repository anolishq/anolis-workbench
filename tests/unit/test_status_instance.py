"""/api/status names the workbench that answers it (#777).

An Operate page loaded before an upgrade keeps running the code and project
it loaded. The page compares the instance it first saw with each later one,
so the status must carry the package version and an id fixed for the life of
the server process.
"""

from __future__ import annotations

import importlib.metadata

from anolis_workbench.server.routes import commission


class _Handler:
    def __init__(self) -> None:
        self.sent: list[tuple[int, dict]] = []

    def _json(self, code: int, payload: dict) -> None:
        self.sent.append((code, payload))


def _status(monkeypatch) -> dict:
    monkeypatch.setattr(commission.launcher_module, "get_status", lambda: {"running": False})
    handler = _Handler()
    commission.status(handler, host="127.0.0.1", port=3010)
    code, payload = handler.sent[0]
    assert code == 200
    return payload["workbench"]


def test_status_names_the_package_version_and_an_instance(monkeypatch) -> None:
    workbench = _status(monkeypatch)
    assert workbench["version"] == 1
    assert workbench["package_version"] == importlib.metadata.version("anolis-workbench")
    assert isinstance(workbench["instance"], str) and len(workbench["instance"]) >= 16


def test_the_instance_is_the_same_for_every_reply_of_one_process(monkeypatch) -> None:
    assert _status(monkeypatch)["instance"] == _status(monkeypatch)["instance"]
