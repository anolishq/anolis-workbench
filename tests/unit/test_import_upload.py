"""Unit tests for #749: importing a project folder uploaded from the browser.

The folder arrives as files with paths relative to it. The server writes them
to a private staging directory, validates and imports the folder as a path
import does, and removes the staging directory whatever happens. A path that
is not a plain relative path, too many files or too many bytes are refused
before anything is written.
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest

from anolis_workbench.core import projects
from anolis_workbench.server import app
from anolis_workbench.server.routes import compose

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "imported-profile"


def _files(root: pathlib.Path) -> list[dict[str, str]]:
    return [
        {"path": p.relative_to(root).as_posix(), "content": base64.b64encode(p.read_bytes()).decode()}
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]


def _digests(root: pathlib.Path, *, exclude: tuple[str, ...] = ()) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.relative_to(root).as_posix() not in exclude
    }


@pytest.fixture()
def systems_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "data" / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)
    return root


def _staged(systems_root: pathlib.Path) -> list[pathlib.Path]:
    staging = systems_root.parent / "upload-staging"
    return list(staging.iterdir()) if staging.exists() else []


def test_an_uploaded_folder_imports_byte_identical_and_leaves_no_staging(systems_root: pathlib.Path) -> None:
    meta, warnings = projects.import_uploaded_project("imported-profile", _files(FIXTURE), "rig-a")

    assert warnings == []
    assert meta["profile_dir_name"] == "imported-profile"
    assert meta["imported_from"] == "browser upload (imported-profile)"
    imported = systems_root / "rig-a"
    expected = {
        rel: d
        for rel, d in _digests(FIXTURE).items()
        if rel == "machine-profile.yaml" or rel.startswith(("config/", "behaviors/"))
    }
    assert _digests(imported, exclude=(projects.SIDECAR_NAME,)) == expected
    assert _staged(systems_root) == []


def test_the_name_defaults_to_the_folder_name(systems_root: pathlib.Path) -> None:
    meta, _ = projects.import_uploaded_project("imported-profile", _files(FIXTURE))
    assert meta["name"] == "imported-profile"


@pytest.mark.parametrize(
    "bad",
    ["../escape.yaml", "/etc/passwd", "config/../../escape.yaml", "a\\b.yaml", "", "config//x.yaml", ".", "x\x00y"],
    ids=repr,
)
def test_a_path_that_is_not_plain_and_relative_is_refused_before_anything_is_written(
    systems_root: pathlib.Path, bad: str
) -> None:
    files = _files(FIXTURE) + [{"path": bad, "content": base64.b64encode(b"x").decode()}]

    with pytest.raises(projects.ImportValidationError) as exc:
        projects.import_uploaded_project("imported-profile", files, "rig-a")

    assert "path" in exc.value.errors[0]
    assert not (systems_root / "rig-a").exists()
    assert _staged(systems_root) == []
    assert not (systems_root.parent / "escape.yaml").exists()


@pytest.mark.parametrize("folder", ["..", "a/b", "has space", "", 7], ids=repr)
def test_a_folder_name_that_is_not_a_plain_name_is_refused(systems_root: pathlib.Path, folder: Any) -> None:
    with pytest.raises(projects.ImportValidationError):
        projects.import_uploaded_project(folder, _files(FIXTURE), "rig-a")
    assert not (systems_root / "rig-a").exists()


def test_too_many_files_are_refused(systems_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(projects, "UPLOAD_MAX_FILES", 3)
    with pytest.raises(projects.ImportValidationError) as exc:
        projects.import_uploaded_project("imported-profile", _files(FIXTURE), "rig-a")
    assert "files" in exc.value.errors[0]
    assert _staged(systems_root) == []


def test_too_many_bytes_are_refused(systems_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(projects, "UPLOAD_MAX_BYTES", 100)
    with pytest.raises(projects.ImportValidationError) as exc:
        projects.import_uploaded_project("imported-profile", _files(FIXTURE), "rig-a")
    assert "bytes" in exc.value.errors[0]
    assert _staged(systems_root) == []


def test_content_that_is_not_base64_is_refused(systems_root: pathlib.Path) -> None:
    files = _files(FIXTURE) + [{"path": "notes.txt", "content": "not base64!"}]
    with pytest.raises(projects.ImportValidationError):
        projects.import_uploaded_project("imported-profile", files, "rig-a")
    assert _staged(systems_root) == []


def test_the_same_path_twice_is_refused(systems_root: pathlib.Path) -> None:
    files = _files(FIXTURE)
    with pytest.raises(projects.ImportValidationError):
        projects.import_uploaded_project("imported-profile", files + files[:1], "rig-a")
    assert _staged(systems_root) == []


def test_a_folder_that_fails_validation_is_not_imported_and_leaves_no_staging(systems_root: pathlib.Path) -> None:
    files = [f for f in _files(FIXTURE) if not f["path"].startswith("behaviors/")]

    with pytest.raises(projects.ImportValidationError):
        projects.import_uploaded_project("imported-profile", files, "rig-a")

    assert not (systems_root / "rig-a").exists()
    assert _staged(systems_root) == []


def test_an_existing_name_is_refused(systems_root: pathlib.Path) -> None:
    projects.import_uploaded_project("imported-profile", _files(FIXTURE), "rig-a")
    with pytest.raises(ValueError, match="already exists"):
        projects.import_uploaded_project("imported-profile", _files(FIXTURE), "rig-a")


# ---------------------------------------------------------------------------
# POST /api/projects/import-upload
# ---------------------------------------------------------------------------


class FakeHandler:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.max_bytes: int | None = None
        self.responses: list[tuple[int, dict[str, Any]]] = []

    def _body_json(self, max_bytes: int = 1_048_576) -> dict[str, Any]:
        self.max_bytes = max_bytes
        return self.body

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self.responses.append((status, data))


def test_the_route_imports_and_answers_201(systems_root: pathlib.Path) -> None:
    handler = FakeHandler({"folder": "imported-profile", "files": _files(FIXTURE), "name": "rig-a"})

    compose.import_uploaded_project(handler)

    status, data = handler.responses[0]
    assert status == 201, data
    assert data["name"] == "rig-a"
    assert handler.max_bytes == compose.UPLOAD_BODY_MAX_BYTES


def test_the_route_answers_400_with_the_reason_for_a_bad_path(systems_root: pathlib.Path) -> None:
    handler = FakeHandler({"folder": "imported-profile", "files": [{"path": "../x", "content": ""}], "name": "rig-a"})

    compose.import_uploaded_project(handler)

    status, data = handler.responses[0]
    assert status == 400
    assert data["code"] == "import_validation_failed"


def test_the_body_limit_is_the_callers_and_one_mib_by_default() -> None:
    def call(length: int, **kwargs: Any) -> list[tuple[int, Any]]:
        out: list[tuple[int, Any]] = []
        raw = json.dumps({"x": "y" * max(0, length - 10)}).encode()
        fake = SimpleNamespace(
            headers={"Content-Length": str(len(raw))},
            rfile=BytesIO(raw),
            _json=lambda s, d: out.append((s, d)),
        )
        result = app._Handler._body_json(fake, **kwargs)  # type: ignore[arg-type]
        out.append((0, result is not None))
        return out

    assert call(2_000_000)[0][0] == 400
    assert call(2_000_000, max_bytes=4_194_304) == [(0, True)]
