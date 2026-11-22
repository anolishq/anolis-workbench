"""Bundle exports and provision jobs are bounded on a long-running server (#674).

`deploy.stage_bundle` is stubbed to write a tarball into the directory it is
given, as `install.sh --stage` does, so these need no network.
"""

from __future__ import annotations

import pathlib
import tempfile
from typing import Any

import pytest

from anolis_workbench.core import deploy
from anolis_workbench.server.routes import provision


@pytest.fixture()
def tmp_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    monkeypatch.setattr(provision, "_jobs", {})
    monkeypatch.setattr(provision, "_bundle_artifacts", {})
    monkeypatch.setattr(provision, "_bundle_dirs", {}, raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(provision, "_prepare_workspace", lambda params, progress, allow_replace=True: workspace)
    return root


def _staged_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(root.glob("anolis-bundle-*"))


def _stage_ok(**kwargs: Any) -> pathlib.Path:
    tarball: pathlib.Path = kwargs["out_dir"] / "anolis-demo-x86_64.tar.gz"
    tarball.write_bytes(b"\0" * 1024)
    return tarball


def _export(monkeypatch: pytest.MonkeyPatch) -> provision.ProvisionJob:
    monkeypatch.setattr(deploy, "stage_bundle", _stage_ok)
    job = provision._create_job()
    provision._run_bundle_job(job, {"project": "demo", "arch": "x86_64"})
    assert job.status == "done"
    return job


def test_a_failed_export_removes_its_staging_directory(tmp_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _stage_fails(**kwargs: Any) -> pathlib.Path:
        (kwargs["out_dir"] / "partial.tar.gz").write_bytes(b"partial")
        raise deploy.DeployError("install.sh --stage failed")

    monkeypatch.setattr(deploy, "stage_bundle", _stage_fails)
    job = provision._create_job()
    provision._run_bundle_job(job, {"project": "demo", "arch": "x86_64"})

    assert job.status == "failed"
    assert _staged_dirs(tmp_root) == []


def test_only_the_latest_bundles_are_kept(tmp_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [_export(monkeypatch) for _ in range(4)]

    assert len(_staged_dirs(tmp_root)) == provision._KEEP_BUNDLES == 2
    kept = [job.job_id for job in jobs[-2:]]
    assert list(provision._bundle_artifacts) == kept
    for job_id in kept:
        assert provision._bundle_artifacts[job_id].is_file()
    for job in jobs[:2]:
        assert job.job_id not in provision._bundle_artifacts


def test_finished_jobs_are_bounded_and_an_evicted_job_takes_its_bundle(
    tmp_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provision, "_KEEP_FINISHED_JOBS", 3)
    monkeypatch.setattr(provision, "_KEEP_BUNDLES", 10)
    first = _export(monkeypatch)
    assert first.job_id in provision._bundle_artifacts

    for _ in range(5):
        _export(monkeypatch)

    finished = [j for j in provision._jobs.values() if j.status in ("done", "failed")]
    assert len(finished) <= 3
    assert first.job_id not in provision._jobs
    assert first.job_id not in provision._bundle_artifacts
    assert len(_staged_dirs(tmp_root)) == len(provision._bundle_artifacts)


def test_a_running_job_is_never_evicted(tmp_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provision, "_KEEP_FINISHED_JOBS", 1)
    running = provision._create_job()
    for _ in range(3):
        _export(monkeypatch)
    assert running.job_id in provision._jobs


class _Handler:
    def __init__(self) -> None:
        self.sent: list[tuple[int, dict[str, Any]]] = []

    def _json(self, code: int, body: dict[str, Any]) -> None:
        self.sent.append((code, body))


def test_a_pruned_bundle_download_says_it_was_removed(tmp_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #906: a job that says its bundle is ready answered a bare 404
    once two later exports had pruned it. It now says why, with 410."""
    monkeypatch.setattr(provision, "_jobs", {})
    jobs = [_export(monkeypatch) for _ in range(3)]
    for job in jobs:
        with provision._jobs_lock:
            provision._jobs[job.job_id] = job
    provision._keep_latest_bundles()

    first = jobs[0]
    assert first.status == "done"
    assert first.job_id not in provision._bundle_artifacts
    handler = _Handler()
    provision.download_bundle(handler, first.job_id)
    assert handler.sent and handler.sent[0][0] == 410
    assert "anolis-demo-x86_64.tar.gz was removed" in handler.sent[0][1]["error"]
    assert "Export it again" in handler.sent[0][1]["error"]
