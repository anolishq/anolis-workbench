"""How preflight reads a failed --check-config (#852).

A config the runtime refuses read as "Not yet available" when its stderr held
"unknown" anywhere, such as the runtime's unknown-key warning, and the error
was cut to 200 characters. A stub binary prints what the runtime prints.
"""

import pathlib

import pytest

from anolis_workbench.core import launcher
from anolis_workbench.core import paths as paths_module


def _stub(tmp_path: pathlib.Path, stderr: str, code: int) -> pathlib.Path:
    exe = tmp_path / "anolis-runtime"
    script = "#!/bin/sh\ncat >&2 <<'EOF'\n" + stderr + "EOF\nexit " + str(code) + "\n"
    exe.write_text(script, encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def config(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.setattr(paths_module, "DATA_ROOT", tmp_path)
    path = tmp_path / "runtime.yaml"
    path.write_text("{}\n", encoding="utf-8")
    return path


def test_a_refused_config_is_a_failure(tmp_path: pathlib.Path, config: pathlib.Path) -> None:
    exe = _stub(tmp_path, "ERROR: Duplicate provider ID: 'dup0'\n", 1)
    result = launcher._check_config_binary("Runtime --check-config", exe, config)
    assert result["ok"] is False
    assert result["error"] == "ERROR: Duplicate provider ID: 'dup0'"


def test_an_unknown_key_warning_does_not_hide_the_error(tmp_path: pathlib.Path, config: pathlib.Path) -> None:
    exe = _stub(
        tmp_path,
        "[Config] Unknown top-level key: 'some_typo_key' (will be ignored)\nERROR: Duplicate provider ID: 'dup0'\n",
        1,
    )
    result = launcher._check_config_binary("Runtime --check-config", exe, config)
    assert result["ok"] is False
    assert result["error"] == "ERROR: Duplicate provider ID: 'dup0'"


def test_the_last_error_is_shown_in_full(tmp_path: pathlib.Path, config: pathlib.Path) -> None:
    long_error = "ERROR: " + "x" * 300
    exe = _stub(tmp_path, "ERROR: first\n" + long_error + "\n", 1)
    result = launcher._check_config_binary("Runtime --check-config", exe, config)
    assert result["error"] == long_error


def test_a_binary_without_the_verb_is_not_yet_available(tmp_path: pathlib.Path, config: pathlib.Path) -> None:
    exe = _stub(tmp_path, "Unknown argument: --check-config\n", 1)
    result = launcher._check_config_binary("Runtime --check-config", exe, config)
    assert result["ok"] is None
    assert result["note"] == "Not yet available"
