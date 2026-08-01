"""Smoke tests for launcher.preflight's provider --check-config path (#270).

Preflight previously had no python coverage at all; these tests pin the
part 2b-relevant behavior: provider configs are re-rendered verbatim from the
native config and every provider binary gets an unconditional --check-config
probe with graceful degradation for binaries that predate the verb.
"""

import json
import pathlib
import sys

import pytest
import yaml

from anolis_workbench.core import launcher

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses /bin/sh stub binaries")

TEMPLATES = pathlib.Path(__file__).parent.parent.parent / "anolis_workbench" / "templates"


def _fake_binary(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _make_system(tmp_path: pathlib.Path, provider_body: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    # --check-config subprocesses run with cwd=DATA_ROOT (~/.anolis), which
    # does not exist on CI runners — point it at the sandbox.
    monkeypatch.setattr("anolis_workbench.core.paths.DATA_ROOT", tmp_path)
    system: dict = json.loads((TEMPLATES / "sim-quickstart" / "system.json").read_text(encoding="utf-8"))
    runtime_bin = _fake_binary(tmp_path, "anolis-runtime", 'test "$1" = --check-config && test -f "$2" && exit 0')
    provider_bin = _fake_binary(tmp_path, "anolis-provider-sim", provider_body)
    system["paths"]["runtime_executable"] = str(runtime_bin)
    system["paths"]["providers"]["sim0"]["executable"] = str(provider_bin)
    system["topology"]["runtime"]["http_port"] = 39321  # avoid a false port-in-use failure
    return system


def _check(result: dict, name: str) -> dict:
    matches: list[dict] = [c for c in result["checks"] if c["name"] == name]
    assert matches, [c["name"] for c in result["checks"]]
    return matches[0]


def test_preflight_renders_native_config_and_check_config_passes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = _make_system(tmp_path, 'test "$1" = --check-config && test -f "$2" && exit 0', monkeypatch)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    result = launcher.preflight("smoke", system, project_dir)

    rendered = yaml.safe_load((project_dir / "providers" / "sim0.yaml").read_text(encoding="utf-8"))
    assert rendered == system["topology"]["providers"]["sim0"]["config"]  # verbatim passthrough

    assert _check(result, "Provider sim0 --check-config")["ok"] is True
    assert _check(result, "Runtime --check-config")["ok"] is True
    assert result["ok"] is True


def test_preflight_reports_provider_config_rejection(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    system = _make_system(tmp_path, 'echo "config invalid: bad tick" >&2; exit 1', monkeypatch)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    result = launcher.preflight("smoke", system, project_dir)

    check = _check(result, "Provider sim0 --check-config")
    assert check["ok"] is False
    assert "config invalid" in (check["error"] or "")
    assert result["ok"] is False


def test_preflight_degrades_for_binaries_without_the_verb(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = _make_system(tmp_path, 'echo "unknown option: --check-config" >&2; exit 1', monkeypatch)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    result = launcher.preflight("smoke", system, project_dir)

    check = _check(result, "Provider sim0 --check-config")
    assert check["ok"] is None
    assert check["note"] == "Not yet available"
    assert result["ok"] is True  # a missing verb must not fail preflight
