"""The project install.sh put on this machine, imported at startup (#722)."""

from __future__ import annotations

import pathlib

from anolis_workbench.core import paths as paths_module


def sync(prefix: pathlib.Path = paths_module.DEFAULT_INSTALL_PREFIX) -> str | None:
    """Import or re-sync the installed project; its name, or None."""
    return None
