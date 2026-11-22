"""Every Node version a workflow sets up is one frontend/package.json's
engines.node allows. #877 raised engines past Node 20 while five setup-node
steps stayed on it.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]


def _engines_range() -> str:
    package = json.loads((REPO / "frontend" / "package.json").read_text(encoding="utf-8"))
    return str(package["engines"]["node"])


def _allows(range_text: str, major: int) -> bool:
    """Whether some release of `major` satisfies `range_text`, for the forms
    engines uses: ^X.Y.Z and >=X.Y.Z joined by ||."""
    for part in (p.strip() for p in range_text.split("||")):
        caret = re.fullmatch(r"\^(\d+)\.\d+\.\d+", part)
        at_least = re.fullmatch(r">=(\d+)\.\d+\.\d+", part)
        if caret and int(caret[1]) == major:
            return True
        if at_least and major >= int(at_least[1]):
            return True
        if not caret and not at_least:
            raise AssertionError(f"engines.node part {part!r} is not a form this test reads")
    return False


def _setup_node_versions() -> list[tuple[str, str]]:
    found = []
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (workflow.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).startswith("actions/setup-node@"):
                    found.append((path.name, str((step.get("with") or {}).get("node-version"))))
    return found


def test_workflows_set_up_node() -> None:
    assert _setup_node_versions(), "no setup-node step found; the scan below would check nothing"


@pytest.mark.parametrize(("workflow", "version"), _setup_node_versions())
def test_each_setup_node_version_is_one_engines_allows(workflow: str, version: str) -> None:
    major = re.match(r"(\d+)", version)
    assert major, f"{workflow}: node-version {version!r} has no major version"
    assert _allows(_engines_range(), int(major[1])), (
        f"{workflow}: node-version {version} is outside engines.node {_engines_range()!r}"
    )
