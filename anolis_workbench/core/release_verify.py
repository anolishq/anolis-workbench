"""Check a downloaded install.sh against its release's SHA256SUMS (#530).

install.sh runs as root, locally under sudo or on the target over ssh, so the
workbench checks it the way install.sh checks the tarballs it downloads
(anolishq/anolis#408): against the SHA256SUMS published with the same release.
"""

from __future__ import annotations

import hashlib
import re

import requests

INSTALL_SH = "install.sh"


class ReleaseVerificationError(Exception):
    """install.sh does not match its release's SHA256SUMS, or SHA256SUMS could not be read."""


def _expected_digest(sums: str, asset: str) -> str | None:
    """The digest SHA256SUMS lists for ``asset`` (standard sha256sum output), or None."""
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("*") == asset:
            return parts[0].lower()
    return None


# The oldest anolis release the workbench deploys: the first whose SHA256SUMS
# lists install.sh (anolishq/anolis#449). Releases through v0.1.49 do not.
OLDEST_VERIFIABLE_RELEASE = "0.1.50"


_RELEASE_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def require_release_version(version: object, where: str) -> str:
    """Return ``version`` if it is a plain X.Y.Z release number, else raise.

    A version goes into a release download URL. Anything else (a suffix,
    a path separator, a scheme) could point the download, and the checksum
    that vouches for it, somewhere other than the release the profile names.
    """
    if not isinstance(version, str) or _RELEASE_VERSION_RE.fullmatch(version) is None:
        raise ReleaseVerificationError(f"{where} must be a release number X.Y.Z, got {version!r}")
    return version


def _unverifiable(repo: str, version: str, what: str) -> ReleaseVerificationError:
    product = repo.rsplit("/", 1)[-1]
    return ReleaseVerificationError(
        f"{product} v{version} has {what}, so install.sh cannot be verified; refusing to run it. "
        f"Pin {product} v{OLDEST_VERIFIABLE_RELEASE} or later (#533)."
    )


def verify_install_sh(repo: str, version: str, content: bytes, *, timeout: float = 30) -> str | None:
    """Check ``content`` against the install.sh line of ``repo``'s v``version`` SHA256SUMS.

    Returns None when it matches. Raises ReleaseVerificationError on a
    mismatch, when the release has no SHA256SUMS (HTTP 404) or no line for
    install.sh, or when SHA256SUMS cannot be fetched (#533).
    """
    require_release_version(version, "the release version")
    url = f"https://github.com/{repo}/releases/download/v{version}/SHA256SUMS"
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise ReleaseVerificationError(
            f"could not fetch SHA256SUMS for {repo} v{version} ({exc}); refusing to run install.sh unverified"
        ) from exc
    if resp.status_code == 404:
        raise _unverifiable(repo, version, "no SHA256SUMS")
    if resp.status_code != 200:
        raise ReleaseVerificationError(
            f"could not fetch SHA256SUMS for {repo} v{version} (HTTP {resp.status_code}); "
            "refusing to run install.sh unverified"
        )
    expected = _expected_digest(resp.content.decode("utf-8", errors="replace"), INSTALL_SH)
    if expected is None:
        raise _unverifiable(repo, version, "no install.sh line in its SHA256SUMS")
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ReleaseVerificationError(
            f"install.sh from {repo} v{version} does not match its SHA256SUMS: expected {expected}, got {actual}"
        )
    return None
