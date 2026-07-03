"""Latest-release lookup for anolis components.

Shared by the exporter (machine-profile component pins) and deploy
(install.sh fetch). Network-best-effort: offline returns None and callers
degrade gracefully.
"""

from __future__ import annotations

import requests

GITHUB_API = "https://api.github.com"
RUNTIME_REPO = "anolishq/anolis"
PROVIDER_ORG = "anolishq"

_RELEASE_CACHE: "dict[str, str | None]" = {}


def provider_repo(kind: str) -> str:
    """Repo for a provider kind (anolishq/anolis-provider-<kind>)."""
    return f"{PROVIDER_ORG}/anolis-provider-{kind}"


def latest_release_version(repo: str) -> "str | None":
    """Latest release tag of ``repo`` (without the ``v`` prefix), or None.

    None means offline / no release — the caller degrades gracefully. Cached
    per process so an export makes at most one API call per repo.
    """
    if repo in _RELEASE_CACHE:
        return _RELEASE_CACHE[repo]
    version: str | None = None
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if resp.status_code == 200:
            tag = resp.json().get("tag_name", "")
            version = str(tag).lstrip("v") or None
    except requests.RequestException:
        version = None
    _RELEASE_CACHE[repo] = version
    return version
