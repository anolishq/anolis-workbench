"""The release cannot publish without its SBOMs (#673).

A release run is the only full test of release.yml, and it publishes. These
tests check what can be checked without one: that the job graph stops on a
failed SBOM job, that the SBOM download fails when the artifact is missing,
and, by running the workflow's own shell steps on a local directory, that the
attest job refuses release assets without the three SBOMs.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
SBOMS = ("sbom-python.cdx.json", "sbom-node.cdx.json", "sbom-rust.cdx.json")
# The jobs whose effects a later failure cannot undo: the PyPI upload, and the
# tag and GitHub Release.
IRREVERSIBLE = ("publish-pypi", "create-release")


def _workflow() -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text("utf-8"))
    return workflow


def _jobs() -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = _workflow()["jobs"]
    return jobs


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _ancestors(name: str) -> set[str]:
    jobs, seen, todo = _jobs(), set(), [name]
    while todo:
        for parent in _needs(jobs[todo.pop()]):
            if parent not in seen:
                seen.add(parent)
                todo.append(parent)
    return seen


def _step(job: str, name: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = _jobs()[job]["steps"]
    return next(s for s in steps if s.get("name") == name)


def _step_names(job: str) -> list[str]:
    return [s.get("name", "") for s in _jobs()[job]["steps"]]


@pytest.mark.parametrize("job", IRREVERSIBLE)
def test_every_irreversible_job_waits_for_the_sbom_job_and_the_attest_job(job: str) -> None:
    assert {"sbom", "attest"} <= _ancestors(job)


@pytest.mark.parametrize("job", IRREVERSIBLE)
def test_nothing_an_irreversible_job_waits_for_may_fail_quietly(job: str) -> None:
    """A job or step with continue-on-error counts as passed for `needs`, and
    an `if:` on the job itself could run it after a failure."""
    jobs = _jobs()
    for name in sorted(_ancestors(job) | {job}):
        assert "continue-on-error" not in jobs[name], f"job {name} is continue-on-error"
        for step in jobs[name].get("steps", []):
            assert "continue-on-error" not in step, f"{name}: step {step.get('name')!r} is continue-on-error"
    for name in sorted(_ancestors(job) | {job}):
        assert "if" not in jobs[name], f"job {name} has its own if: {jobs[name]['if']!r}"


def test_the_sbom_artifact_is_downloaded_by_the_name_it_is_uploaded_under() -> None:
    uploaded = _step("sbom", "Upload SBOM artifacts")["with"]
    downloaded = _step("attest", "Download SBOM artifacts")["with"]
    assert downloaded.get("name") == uploaded["name"]
    assert "pattern" not in downloaded
    assert sorted(uploaded["path"].split()) == sorted(SBOMS)


def test_the_sbom_check_runs_after_the_assets_are_named_and_before_they_are_attested() -> None:
    names = _step_names("attest")
    check = names.index("Require the three SBOMs")
    assert names.index("Normalize desktop artifact filenames") < check < names.index("Attest release assets")


def _lines(block: str) -> list[str]:
    return [line.strip() for line in block.splitlines() if line.strip()]


def test_what_is_attested_uploaded_and_released_is_one_list() -> None:
    """A line dropped from one list ships an asset unattested, or drops it
    from the artifact create-release attaches from, after the tag is pushed."""
    attested = _lines(_step("attest", "Attest release assets")["with"]["subject-path"])
    uploaded = _lines(_step("attest", "Upload attested release assets")["with"]["path"])
    steps = _jobs()["create-release"]["steps"]
    release = next(s for s in steps if str(s.get("uses", "")).startswith("softprops/action-gh-release@"))
    released = _lines(release["with"]["files"])
    assert attested == uploaded == released
    assert "release-assets/sbom-*" in attested


def test_the_release_step_fails_on_an_unmatched_files_line() -> None:
    steps = _jobs()["create-release"]["steps"]
    release = next(s for s in steps if str(s.get("uses", "")).startswith("softprops/action-gh-release@"))
    assert release["with"]["fail_on_unmatched_files"] is True


# ── The attest job's own shell steps, run on a local directory ──────────────

TOOLS = ("bash", "jq")
MISSING_TOOLS = [t for t in TOOLS if shutil.which(t) is None]
# Skipped on a developer machine without them; in CI a skip would pass while
# testing nothing, so there they run, and fail.
needs_tools = pytest.mark.skipif(
    bool(MISSING_TOOLS) and not os.environ.get("CI"), reason=f"needs {', '.join(MISSING_TOOLS)}, as the runner has"
)


@pytest.mark.skipif(not os.environ.get("CI"), reason="CI only: elsewhere the step tests skip instead")
def test_ci_has_the_tools_the_attest_steps_need() -> None:
    assert not MISSING_TOOLS, f"not on PATH: {MISSING_TOOLS}"


def _run_step(job: str, name: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    script = _step(job, name)["run"].replace("${{ inputs.version }}", "9.9.9")
    assert "${{" not in script, "an expression this test does not substitute"
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True, timeout=60)


# The root entry syft writes for frontend/ (v0.33.1's sbom-node.cdx.json).
COMPONENT = {"type": "library", "name": "anolis-workbench-ui", "version": "9.9.9"}


def _sbom(path: pathlib.Path) -> None:
    path.write_text(json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [COMPONENT]}), "utf-8")


def _downloaded(tmp_path: pathlib.Path, *, sboms: tuple[str, ...]) -> pathlib.Path:
    """raw-bundles as the attest job's download steps leave it."""
    raw = tmp_path / "raw-bundles"
    raw.mkdir()
    for bundle in ("Anolis Workbench_9.9.9_x64_en-US.msi", "anolis-workbench_9.9.9_amd64.deb"):
        (raw / bundle).write_bytes(b"bundle")
    for name in sboms:
        _sbom(raw / name)
    return tmp_path


def _attest_steps(cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    normalized = _run_step("attest", "Normalize desktop artifact filenames", cwd)
    assert normalized.returncode == 0, normalized.stderr
    return _run_step("attest", "Require the three SBOMs", cwd)


@needs_tools
def test_release_assets_with_the_three_sboms_pass(tmp_path: pathlib.Path) -> None:
    result = _attest_steps(_downloaded(tmp_path, sboms=SBOMS))
    assert result.returncode == 0, result.stdout + result.stderr
    assert {p.name for p in (tmp_path / "release-assets").glob("sbom-*")} == set(SBOMS)


@needs_tools
def test_release_assets_without_sboms_are_refused(tmp_path: pathlib.Path) -> None:
    result = _attest_steps(_downloaded(tmp_path, sboms=()))
    assert result.returncode != 0
    for name in SBOMS:
        assert f"::error::release-assets/{name} is missing" in result.stdout


@needs_tools
@pytest.mark.parametrize("missing", SBOMS)
def test_release_assets_missing_one_sbom_are_refused(tmp_path: pathlib.Path, missing: str) -> None:
    result = _attest_steps(_downloaded(tmp_path, sboms=tuple(s for s in SBOMS if s != missing)))
    assert result.returncode != 0
    assert result.stdout.count("::error::") == 1
    assert f"release-assets/{missing}" in result.stdout


@needs_tools
@pytest.mark.parametrize(
    "content",
    [
        "",
        json.dumps({"components": [COMPONENT]}),
        json.dumps({"bomFormat": "SPDX", "components": [COMPONENT]}),
        "not json",
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []}),
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6"}),
    ],
    ids=["empty", "no-bomformat", "spdx", "not-json", "no-components", "components-absent"],
)
def test_an_sbom_that_is_not_a_cyclonedx_inventory_is_refused(tmp_path: pathlib.Path, content: str) -> None:
    cwd = _downloaded(tmp_path, sboms=SBOMS)
    (cwd / "raw-bundles" / "sbom-node.cdx.json").write_text(content, "utf-8")
    result = _attest_steps(cwd)
    assert result.returncode != 0
    assert result.stdout.count("::error::") == 1
    assert (
        "release-assets/sbom-node.cdx.json is missing, is not a CycloneDX SBOM, or lists no components" in result.stdout
    )
