"""A project name is the whole string, with no trailing newline (review of #920).

`^...$` with re.match let `$` match before a final newline, so `rig\\n` was a
valid name and reached paths and commands built from it.
"""

from __future__ import annotations

import pytest

from anolis_workbench.core import projects


@pytest.mark.parametrize("name", ["rig\n", "rig\r\n", "\nrig", "rig\n\n"])
def test_a_name_with_a_newline_is_refused(name: str) -> None:
    assert projects.validate_name(name) is not None


@pytest.mark.parametrize("name", ["rig", "bioreactor-v1", "a_b", "x" * 64])
def test_a_plain_name_is_accepted(name: str) -> None:
    assert projects.validate_name(name) is None
