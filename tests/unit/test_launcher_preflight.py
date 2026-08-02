"""Smoke tests for launcher.preflight's provider --check-config path.

Two properties matter here. From #270: every provider binary gets an
unconditional --check-config probe, with graceful degradation for binaries that
predate the verb. From #255: launch and preflight project the canonical variant
into a THROWAWAY host config under .workbench/ and never touch the canonical
files, which are the ones that get deployed.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

import pytest
import yaml

from anolis_workbench.core import canonical, launcher

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses /bin/sh stub binaries")


def _fake_binary(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _tree_digests(root: pathlib.Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and canonical.LAUNCH_DIR not in p.parts
    }


@pytest.fixture()
def project(canonical_project, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """A canonical project whose sidecar host paths point at stub binaries."""
    # --check-config subprocesses run with cwd=DATA_ROOT (~/.anolis), which
    # does not exist on CI runners — point it at the sandbox.
    monkeypatch.setattr("anolis_workbench.core.paths.DATA_ROOT", tmp_path)

    def _make(provider_body: str) -> tuple[pathlib.Path, dict]:
        runtime_bin = _fake_binary(tmp_path, "anolis-runtime", 'test "$1" = --check-config && test -f "$2" && exit 0')
        provider_bin = _fake_binary(tmp_path, "anolis-provider-sim", provider_body)
        pdir = canonical_project(
            tmp_path / "project",
            machine_id="smoke",
            host_paths={
                "runtime_executable": str(runtime_bin),
                "providers": {"sim0": {"executable": str(provider_bin)}},
            },
        )
        # Avoid a false port-in-use failure.
        variant = pdir / canonical.variant_relpath(canonical.MANUAL_VARIANT)
        doc = yaml.safe_load(variant.read_text(encoding="utf-8"))
        doc["http"]["port"] = 39321
        variant.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return pdir, canonical.read_project(pdir)

    return _make


def _check(result: dict, name: str) -> dict:
    matches: list[dict] = [c for c in result["checks"] if c["name"] == name]
    assert matches, [c["name"] for c in result["checks"]]
    return matches[0]


def test_preflight_projects_host_paths_and_check_config_passes(project) -> None:
    pdir, document = project('test "$1" = --check-config && test -f "$2" && exit 0')
    before = _tree_digests(pdir)

    result = launcher.preflight("smoke", document, pdir)

    assert _check(result, "Provider sim0 --check-config")["ok"] is True
    assert _check(result, "Runtime --check-config")["ok"] is True
    assert result["ok"] is True

    # The launch projection is a throwaway with the HOST binary substituted for
    # the deploy token; the canonical files are byte-identical afterwards.
    launched = yaml.safe_load(
        (pdir / canonical.LAUNCH_DIR / "launch" / canonical.variant_filename(canonical.MANUAL_VARIANT)).read_text(
            encoding="utf-8"
        )
    )
    entry = launched["providers"][0]
    assert entry["command"].endswith("anolis-provider-sim")
    assert not entry["command"].startswith("../")
    assert entry["args"] == ["--config", str(pdir / canonical.provider_config_relpath("sim", "sim0"))]
    assert _tree_digests(pdir) == before


def test_preflight_reports_provider_config_rejection(project) -> None:
    pdir, document = project('echo "config invalid: bad tick" >&2; exit 1')

    result = launcher.preflight("smoke", document, pdir)

    check = _check(result, "Provider sim0 --check-config")
    assert check["ok"] is False
    assert "config invalid" in (check["error"] or "")
    assert result["ok"] is False


def test_preflight_degrades_for_binaries_without_the_verb(project) -> None:
    pdir, document = project('echo "unknown option: --check-config" >&2; exit 1')

    result = launcher.preflight("smoke", document, pdir)

    check = _check(result, "Provider sim0 --check-config")
    assert check["ok"] is None
    assert check["note"] == "Not yet available"
    assert result["ok"] is True  # a missing verb must not fail preflight
