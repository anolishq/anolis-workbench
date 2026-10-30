"""The project install.sh put on this machine, imported at startup (#722).

install.sh leaves <prefix>/manifest.json, naming the installed profile, a copy
of that profile at <prefix>/projects/<profile>/, and the runtime config it runs
at <prefix>/config/runtime.yaml. When the manifest exists, the workbench
imports that profile as a read-only project marked `installed_from` <prefix>,
and re-syncs it on every start, so Operate reaches the installed runtime with
no import by hand and the copy never goes stale (#558).
"""

from __future__ import annotations

import json
import logging
import pathlib

import yaml

from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module

logger = logging.getLogger(__name__)

# install.sh's DEFAULT_PORT, which its health probe uses when runtime.yaml
# sets no http.port.
DEFAULT_RUNTIME_PORT = 8080


def installed_runtime_port(prefix: pathlib.Path) -> int | None:
    """The http.port of <prefix>/config/runtime.yaml, as install.sh's health
    probe reads it: DEFAULT_RUNTIME_PORT when unset. None when the file cannot
    be read as a mapping."""
    try:
        doc = yaml.safe_load((prefix / "config" / "runtime.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(doc, dict):
        return None
    http = doc.get("http")
    port = http.get("port") if isinstance(http, dict) else None
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
        return port
    return DEFAULT_RUNTIME_PORT


def _installed_profile(prefix: pathlib.Path) -> str | None:
    try:
        manifest = json.loads((prefix / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        logger.warning("installed project: %s/manifest.json cannot be read (%s); nothing imported", prefix, exc)
        return None
    profile = manifest.get("profile") if isinstance(manifest, dict) else None
    if not isinstance(profile, str) or projects_module.validate_name(profile):
        logger.warning("installed project: %s/manifest.json names no usable profile; nothing imported", prefix)
        return None
    return profile


def _project_name(profile: str) -> str | None:
    """`profile`, or `<profile>-installed` when a project of the user's own
    holds that name; None when both are taken by projects that are not ours."""
    for name in (profile, f"{profile}-installed"):
        if not projects_module.project_dir(name).exists() or projects_module.installed_from(name) is not None:
            return name
    return None


def sync(prefix: pathlib.Path = paths_module.DEFAULT_INSTALL_PREFIX) -> str | None:
    """Import or re-sync the installed project; its name, or None (logged why)."""
    if not (prefix / "manifest.json").is_file():
        return None
    profile = _installed_profile(prefix)
    if profile is None:
        return None
    source = prefix / "projects" / profile
    if not (source / "machine-profile.yaml").is_file():
        logger.warning("installed project: %s has no machine-profile.yaml; nothing imported", source)
        return None
    if installed_runtime_port(prefix) is None:
        logger.warning("installed project: %s/config/runtime.yaml cannot be read; nothing imported", prefix)
        return None
    name = _project_name(profile)
    if name is None:
        logger.warning(
            "installed project: projects '%s' and '%s-installed' are both the user's own; nothing imported",
            profile,
            profile,
        )
        return None
    try:
        projects_module.sync_installed_project(source, name, prefix)
    except (OSError, ValueError) as exc:
        logger.warning("installed project: %s not imported (%s)", source, exc)
        return None
    if name != profile:
        logger.info("installed project: imported as '%s'; '%s' is a project of your own", name, profile)
    return name
