"""First-run onboarding detection endpoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _systems_root() -> Path:
    """Return the systems root directory (~/.anolis/systems)."""
    return Path.home() / ".anolis" / "systems"


def _has_projects() -> bool:
    """Check if any project directories exist under ~/.anolis/systems/."""
    root = _systems_root()
    if not root.is_dir():
        return False
    return any(p.is_dir() for p in root.iterdir())


def _runtime_path() -> str:
    """Return the expected runtime binary path."""
    return os.environ.get("ANOLIS_RUNTIME_PATH", "/usr/local/bin/anolis-runtime")


def _has_runtime() -> bool:
    """Check if the runtime binary exists."""
    return Path(_runtime_path()).is_file()


def get_onboarding_status(handler: Any) -> None:
    """GET /api/onboarding — return first-run detection status."""
    has_projects = _has_projects()
    has_runtime = _has_runtime()
    first_run = not has_projects and not has_runtime

    data = {
        "first_run": first_run,
        "has_projects": has_projects,
        "has_runtime": has_runtime,
        "runtime_path": _runtime_path(),
    }
    handler._json(200, data)
