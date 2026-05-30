"""Update detection: compare installed version against latest GitHub release."""

from __future__ import annotations

import importlib.metadata
import logging
from dataclasses import dataclass
from typing import Optional

import requests

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
