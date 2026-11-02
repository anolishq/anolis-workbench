"""Update detection: compare the installed version against the latest GitHub release,
and say how to upgrade this install (#535). Nothing is downloaded or run."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from anolis_workbench.core.workbench_service import SERVICE_NAME

logger = logging.getLogger(__name__)

GITHUB_REPO = "anolishq/anolis-workbench"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
REQUEST_TIMEOUT = 10  # seconds


@dataclass
class UpdateStatus:
    current_version: str
    latest_version: Optional[str]
    update_available: bool
    error: Optional[str] = None


def get_current_version() -> str:
    """Return the installed workbench version."""
    return importlib.metadata.version("anolis-workbench")


def fetch_latest_version() -> Optional[str]:
    """Query GitHub API for the latest release tag. Returns version string or None on failure."""
    try:
        resp = requests.get(
            RELEASES_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        tag = resp.json().get("tag_name", "")
        # Strip leading 'v' if present
        return tag.lstrip("v") if tag else None
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("Failed to check for updates: %s", exc)
        return None


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a semver-like string into a tuple of ints for comparison."""
    parts = []
    for segment in v.split("."):
        # Handle pre-release suffixes like 0.12.0-rc1
        num = ""
        for ch in segment:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


def check_for_update() -> UpdateStatus:
    """Check if an update is available. Safe to call from any context."""
    current = get_current_version()
    latest = fetch_latest_version()

    if latest is None:
        return UpdateStatus(
            current_version=current,
            latest_version=None,
            update_available=False,
            error="Could not reach GitHub to check for updates",
        )

    update_available = _parse_version(latest) > _parse_version(current)
    return UpdateStatus(
        current_version=current,
        latest_version=latest,
        update_available=update_available,
    )


@dataclass(frozen=True)
class UpgradeInstructions:
    """How to upgrade the workbench this process runs from (#535).

    The workbench doesn't upgrade itself: it says what to run. `command` is a
    shell command, or None for the desktop app, whose note names the release
    to download; `restart` is the command that restarts it afterwards, when
    that is known.
    """

    # "pipx", "uv-tool", "pip" (a venv or a plain interpreter) or "desktop"
    install: str
    command: Optional[str]
    restart: Optional[str]
    note: str


def _has_pip() -> bool:
    """True when this interpreter can run `-m pip`; uv makes venvs without it."""
    return importlib.util.find_spec("pip") is not None


def upgrade_instructions(version: Optional[str]) -> UpgradeInstructions:
    """What upgrades this install to `version`, or to the latest when None."""
    if getattr(sys, "frozen", False):
        # The desktop app's frozen sidecar: a new build replaces the app.
        where = (
            f"https://github.com/{GITHUB_REPO}/releases/tag/v{version}"
            if version
            else f"https://github.com/{GITHUB_REPO}/releases/latest"
        )
        return UpgradeInstructions("desktop", None, None, f"Install the new desktop build from {where}.")

    prefix = Path(sys.prefix)
    requirement = f"anolis-workbench=={version}" if version else "anolis-workbench"
    python = shlex.quote(sys.executable)
    if prefix.parent.name == "venvs" and "pipx" in prefix.parts:
        install, command = "pipx", "pipx upgrade anolis-workbench"
    elif prefix.parent.name == "tools" and "uv" in prefix.parts:
        install, command = "uv-tool", "uv tool upgrade anolis-workbench"
    elif _has_pip():
        install, command = "pip", f"{python} -m pip install --upgrade {requirement}"
    else:
        install, command = "pip", f"uv pip install --python {python} --upgrade {requirement}"

    # systemd sets INVOCATION_ID for the processes of a unit it runs, which is
    # how `anolis-provision workbench-service` runs the workbench.
    if os.environ.get("INVOCATION_ID"):
        restart: Optional[str] = f"sudo systemctl restart {SERVICE_NAME.removesuffix('.service')}"
        note = "Run the command, then restart the workbench service."
    else:
        restart = None
        note = "Run the command, then restart the workbench."
    return UpgradeInstructions(install, command, restart, note)
