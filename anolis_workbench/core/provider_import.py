"""Importing a provider's config-schema envelope from a file (#286, #783).

An operator with a provider the workbench doesn't package, or a pinned
version it doesn't package, imports that provider's `--config-schema`
envelope. The workbench then validates a project pinning that kind at that
version against it (`provider_schemas.resolve`).

The boundary: an import is trusted as much as the user who runs it, and it
never leaves this host. A bad file must still not break the server, read
files or reach the network, so every import, and every load of one, passes
`check_envelope`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import tempfile
from dataclasses import dataclass
from typing import Any

import jsonschema

from anolis_workbench.core import canonical
from anolis_workbench.core import paths as paths_module

ENVELOPE_SUFFIX = ".config-schema.json"
SIDECAR_SUFFIX = ".source.json"

# The packaged envelopes are 5-8 KiB at nesting depth 9-10 (sim, ezo, bread
# 0.10.0); these caps leave ~30x and ~3x headroom.
MAX_ENVELOPE_BYTES = 256 * 1024
MAX_DEPTH = 32
# Both go into a file name, which most file systems cap at 255 bytes.
MAX_KIND_CHARS = 64
MAX_VERSION_CHARS = 128

# The kind names the deploy token (`anolis-provider-<kind>`), a file name, and
# a runtime-config file name that install.sh splits at its first dot.
# Always fullmatch: `$` alone also matches before a trailing newline.
KIND_PATTERN = re.compile(r"[a-z0-9-]+")
PROVIDER_PREFIX = "anolis-provider-"
# semver.org's own pattern.
SEMVER_PATTERN = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)
# jsonschema resolves these, remotely or from file:// (#286's review).
_REF_KEYS = ("$ref", "$dynamicRef", "$recursiveRef")


class ImportRefused(ValueError):
    """The file is not an envelope the workbench will import."""


@dataclass(frozen=True)
class ImportResult:
    kind: str
    version: str
    # "imported", "replaced", "unchanged" or "already packaged"
    status: str
    sha256: str
    path: pathlib.Path | None


@dataclass(frozen=True)
class ImportEntry:
    file: str
    kind: str | None
    version: str | None
    sha256: str | None
    origin: str | None
    imported_at_utc: str | None
    error: str | None


def semver_key(version: str) -> tuple[Any, ...]:
    """Sort key in semver precedence: the numeric core, then a release above
    its prereleases, which compare identifier by identifier, numeric ones
    numerically and below alphanumeric ones, a shorter run below a longer."""
    match = SEMVER_PATTERN.fullmatch(version)
    if not match:
        return (-1, -1, -1, 0, ())
    major, minor, patch, prerelease = match.group(1), match.group(2), match.group(3), match.group(4)
    identifiers = tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part) for part in (prerelease or "").split(".") if part
    )
    return (int(major), int(minor), int(patch), 0 if prerelease else 1, identifiers)


def _depth(node: Any) -> int:
    if isinstance(node, dict):
        return 1 + max((_depth(v) for v in node.values()), default=0)
    if isinstance(node, list):
        return 1 + max((_depth(v) for v in node), default=0)
    return 0


def _has_non_finite(node: Any) -> bool:
    if isinstance(node, float):
        return not math.isfinite(node)
    if isinstance(node, dict):
        return any(_has_non_finite(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_non_finite(v) for v in node)
    return False


def _ref_paths(node: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _REF_KEYS:
                found.append(f"{path}.{key}")
            found.extend(_ref_paths(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            found.extend(_ref_paths(value, f"{path}[{idx}]"))
    return found


def check_envelope(raw: bytes) -> tuple[str, str, dict[str, Any]]:
    """The (kind, version, envelope) a file declares, or ImportRefused."""
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise ImportRefused(f"the file is {len(raw)} bytes; an envelope may be at most {MAX_ENVELOPE_BYTES} bytes")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ImportRefused(f"the file is not JSON the workbench can read: {exc}") from exc
    if not isinstance(doc, dict):
        raise ImportRefused("the file is not a JSON object")
    # Before anything walks it: a pathological nesting would otherwise reach
    # the recursion limit in the checks below.
    if _depth(doc) > MAX_DEPTH:
        raise ImportRefused(f"the envelope is nested too deep (more than {MAX_DEPTH} levels)")
    # json.loads takes NaN, Infinity and 1e999 (as infinity), and the server's
    # JSON replies would carry them back as bare tokens no browser can parse.
    if _has_non_finite(doc):
        raise ImportRefused("the file has NaN, Infinity or a number too large; numbers must be finite")

    version_field = doc.get("config_schema_version")
    if isinstance(version_field, bool) or not isinstance(version_field, int) or version_field < 1:
        raise ImportRefused("config_schema_version must be an integer >= 1")
    schema = doc.get("schema")
    if not isinstance(schema, dict):
        raise ImportRefused("the envelope must carry a JSON-object 'schema'")

    provider = doc.get("provider")
    if not isinstance(provider, str) or not provider.startswith(PROVIDER_PREFIX):
        raise ImportRefused(f"'provider' must name the binary, {PROVIDER_PREFIX}<kind>")
    kind = provider[len(PROVIDER_PREFIX) :]
    if len(kind) > MAX_KIND_CHARS:
        raise ImportRefused(f"the kind is too long (more than {MAX_KIND_CHARS} characters)")
    if not KIND_PATTERN.fullmatch(kind):
        raise ImportRefused(f"the kind {kind!r} must be lowercase letters, digits and hyphens")

    version = doc.get("provider_version")
    if not isinstance(version, str):
        raise ImportRefused("the envelope must carry the provider_version it came from")
    if len(version) > MAX_VERSION_CHARS:
        raise ImportRefused(f"provider_version is too long (more than {MAX_VERSION_CHARS} characters)")
    if not SEMVER_PATTERN.fullmatch(version):
        raise ImportRefused(f"provider_version {version!r} is not a semver version such as 1.2.3")

    refs = _ref_paths(doc)
    if refs:
        raise ImportRefused(f"$ref is not allowed in an imported envelope (found at {refs[0]})")

    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ImportRefused(f"'schema' is not a valid JSON Schema: {exc.message}") from exc
    # A regex re.compile can't take raises OverflowError (a repeat count over
    # 2**32), not re.error, and check_schema lets it through.
    except (OverflowError, RecursionError, re.error) as exc:
        raise ImportRefused(f"'schema' is not a valid JSON Schema: {exc}") from exc
    return kind, version, doc


def stored_name(kind: str, version: str) -> str:
    return f"{kind}@{version}{ENVELOPE_SUFFIX}"


def parse_stored_name(name: str) -> tuple[str, str] | None:
    if not name.endswith(ENVELOPE_SUFFIX):
        return None
    kind, sep, version = name[: -len(ENVELOPE_SUFFIX)].partition("@")
    if not sep or not kind or not version:
        return None
    return kind, version


def _canonical(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def import_file(source: pathlib.Path, *, replace: bool = False) -> ImportResult:
    """Import one envelope file. Raises ImportRefused, writing nothing."""
    from anolis_workbench.core import provider_schemas

    source = pathlib.Path(source)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ImportRefused(f"cannot read {source}: {exc.strerror or exc}") from exc
    kind, version, doc = check_envelope(raw)
    sha256 = hashlib.sha256(raw).hexdigest()

    packaged = provider_schemas.packaged_envelope(kind)
    if packaged is not None and packaged.get("provider_version") == version:
        if _canonical(packaged) == _canonical(doc):
            return ImportResult(kind, version, "already packaged", sha256, None)
        raise ImportRefused(
            f"{kind} {version} is packaged with the workbench, and this file's content differs from it; "
            "an import can't replace a packaged envelope"
        )

    directory = paths_module.IMPORTED_PROVIDER_SCHEMAS_DIR
    target = directory / stored_name(kind, version)
    status = "imported"
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() == sha256:
            return ImportResult(kind, version, "unchanged", sha256, target)
        if not replace:
            raise _already_imported(kind, version)
        status = "replaced"

    sidecar = {
        "origin": str(source.resolve()),
        "sha256": sha256,
        "provider_version": version,
        "imported_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        tmp = _write_temp(directory, target.name, raw)
        try:
            if status == "replaced":
                os.replace(tmp, target)
            elif not _publish_new(tmp, target):
                # Another import of this kind and version landed after the
                # check above.
                if hashlib.sha256(target.read_bytes()).hexdigest() == sha256:
                    return ImportResult(kind, version, "unchanged", sha256, target)
                raise _already_imported(kind, version)
        finally:
            tmp.unlink(missing_ok=True)
        # After the envelope: list_imports drops a sidecar whose sha256
        # disagrees with the envelope beside it, so a reader in between sees
        # no origin rather than a wrong one.
        canonical._write_text_atomic(
            directory / f"{kind}@{version}{SIDECAR_SUFFIX}", json.dumps(sidecar, indent=2) + "\n"
        )
    except OSError as exc:
        raise ImportRefused(f"cannot write into {directory}: {exc.strerror or exc}") from exc
    return ImportResult(kind, version, status, sha256, target)


def _already_imported(kind: str, version: str) -> ImportRefused:
    return ImportRefused(
        f"{kind} {version} is already imported with other content; "
        "pass --replace to replace it (projects pinning it change with it)"
    )


def _write_temp(directory: pathlib.Path, name: str, raw: bytes) -> pathlib.Path:
    """The bytes in a temporary file beside their target, not yet published."""
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{name}.", suffix=".tmp")
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return pathlib.Path(tmp)


def _publish_new(tmp: pathlib.Path, target: pathlib.Path) -> bool:
    """Publish a new import only if nothing is there yet; False if something is.

    os.link fails when the target exists, so two imports of one kind and
    version can't both succeed without --replace. A file system without hard
    links gets an exclusive create instead.
    """
    try:
        os.link(tmp, target)
        return True
    except FileExistsError:
        return False
    except OSError:
        try:
            with open(target, "xb") as handle:
                handle.write(tmp.read_bytes())
            return True
        except FileExistsError:
            return False


def list_imports() -> list[ImportEntry]:
    """Every imported envelope on this host, a bad one with its error."""
    from anolis_workbench.core import provider_schemas

    errors = provider_schemas.import_errors()
    directory = paths_module.IMPORTED_PROVIDER_SCHEMAS_DIR
    entries: list[ImportEntry] = []
    if not directory.is_dir():
        return entries
    for path in sorted(directory.glob(f"*{ENVELOPE_SUFFIX}")):
        parsed = parse_stored_name(path.name)
        kind, version = parsed if parsed else (None, None)
        sidecar: dict[str, Any] = {}
        sidecar_path = path.with_name(path.name[: -len(ENVELOPE_SUFFIX)] + SIDECAR_SUFFIX)
        try:
            loaded = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                sidecar = loaded
        except (OSError, json.JSONDecodeError):
            pass
        try:
            sha256: str | None = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            sha256 = None
        if sidecar.get("sha256") != sha256:
            # Written for other content, by an import that lost a race or
            # was interrupted: its origin and time would be wrong.
            sidecar = {}
        entries.append(
            ImportEntry(
                file=path.name,
                kind=kind,
                version=version,
                sha256=sha256,
                origin=sidecar.get("origin") if isinstance(sidecar.get("origin"), str) else None,
                imported_at_utc=sidecar.get("imported_at_utc")
                if isinstance(sidecar.get("imported_at_utc"), str)
                else None,
                error=errors.get(path.name),
            )
        )
    return entries
