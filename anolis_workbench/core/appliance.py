"""Appliance mode detection for Raspberry Pi environments.

When the workbench is running directly on a Raspberry Pi (aarch64 + device
tree model contains "Raspberry Pi"), it should default to appliance behavior:
- Bind the HTTP server to 0.0.0.0 (accessible from LAN)
- Skip browser auto-open (headless environment)
- Auto-add localhost as the default runtime target
"""

from __future__ import annotations

import platform
from pathlib import Path

_DT_MODEL_PATH = Path("/sys/firmware/devicetree/base/model")


def is_raspberry_pi() -> bool:
    """Detect if running on a Raspberry Pi.

    Checks: architecture is aarch64 AND device tree model contains
    "Raspberry Pi".
    """
    if platform.machine() != "aarch64":
        return False
    try:
        model = _DT_MODEL_PATH.read_text(encoding="utf-8", errors="replace").strip("\x00\n")
        return "Raspberry Pi" in model
    except OSError:
        return False


def is_appliance_mode() -> bool:
    """Detect if workbench should run in appliance mode.

    Appliance mode is active when:
    1. Running on a Raspberry Pi, AND
    2. ANOLIS_WORKBENCH_HOST is not explicitly set (user hasn't overridden)
    """
    import os

    if os.getenv("ANOLIS_WORKBENCH_HOST"):
        return False  # user explicitly set the bind address
    return is_raspberry_pi()


def default_host() -> str:
    """Return the appropriate default bind host.

    Returns "0.0.0.0" in appliance mode, "127.0.0.1" otherwise.
    """
    return "0.0.0.0" if is_appliance_mode() else "127.0.0.1"


def default_open_browser() -> bool:
    """Return whether the browser should auto-open.

    Returns False in appliance mode (headless Pi), True otherwise.
    """
    return not is_appliance_mode()
