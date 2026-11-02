"""CLI entry point for the Anolis Workbench HTTP server."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import pathlib
import sys

from anolis_workbench.core import paths as paths_module
from anolis_workbench.server.app import main as run_server


def _provider(args: argparse.Namespace) -> int:
    """`anolis-workbench provider …` (#286, #783, #786)."""
    from anolis_workbench.core import provider_import

    if args.provider_command == "import":
        geteuid = getattr(os, "geteuid", None)
        if geteuid is not None and geteuid() == 0:
            print(
                f"warning: running as root, so this imports into {paths_module.IMPORTED_PROVIDER_SCHEMAS_DIR}; "
                "a workbench running as another user (the service) won't see it. "
                "Run the import as that user instead.",
                file=sys.stderr,
            )
        try:
            result = provider_import.import_file(pathlib.Path(args.file), replace=args.replace)
        except provider_import.ImportRefused as exc:
            print(f"error: not imported: {exc}", file=sys.stderr)
            return 1
        where = f" into {result.path}" if result.path else ""
        print(f"{result.kind} {result.version}: {result.status} (sha256 {result.sha256[:12]}){where}")
        return 0

    if args.provider_command == "remove":
        try:
            removed = provider_import.remove_import(args.spec, force=args.force)
        except provider_import.RemoveRefused as exc:
            print(f"error: not removed: {exc}", file=sys.stderr)
            return 1
        spec = f"{removed.kind}@{removed.version}"
        if removed.pinned_by:
            print(
                f"warning: removed although project(s) {', '.join(removed.pinned_by)} pin {spec}; "
                "saving them fails until it is imported again.",
                file=sys.stderr,
            )
        if removed.unreadable:
            print(
                f"warning: project(s) {', '.join(removed.unreadable)} can't be read, and may pin {spec}.",
                file=sys.stderr,
            )
        print(f"{spec}: removed {removed.path}")
        return 0

    entries = provider_import.list_imports()
    if not entries:
        print(f"No imported provider schemas in {paths_module.IMPORTED_PROVIDER_SCHEMAS_DIR}.")
        return 0
    for entry in entries:
        if entry.error:
            print(f"BAD {entry.file}: {entry.error}")
            continue
        sha = (entry.sha256 or "?")[:12]
        print(
            f"{entry.kind} {entry.version}  sha256 {sha}  from {entry.origin or '?'}  "
            f"imported {entry.imported_at_utc or '?'}"
        )
    return 0


def main(argv: list[str] | None = None) -> int | None:
    parser = argparse.ArgumentParser(
        prog="anolis-workbench",
        description="Anolis Workbench — commissioning server for Anolis runtime systems.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('anolis-workbench')}",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host (default: 127.0.0.1, overrides ANOLIS_WORKBENCH_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: 3010, overrides ANOLIS_WORKBENCH_PORT).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        default=False,
        help="Do not open a browser window on startup.",
    )
    # With no command it serves, as it always has; the desktop sidecar runs
    # this entry point with only the flags above.
    commands = parser.add_subparsers(dest="command", metavar="{provider}")
    provider = commands.add_parser(
        "provider", help="Import, list or remove provider config schemas the workbench doesn't package."
    )
    provider_commands = provider.add_subparsers(dest="provider_command", required=True)
    import_cmd = provider_commands.add_parser(
        "import", help="Import a provider's --config-schema envelope from a file."
    )
    import_cmd.add_argument("file", help="The envelope file (the provider's --config-schema output).")
    import_cmd.add_argument(
        "--replace",
        action="store_true",
        help="Replace an import of the same kind and version with different content.",
    )
    provider_commands.add_parser("list", help="List imported provider schemas, and any that fail to load.")
    remove_cmd = provider_commands.add_parser(
        "remove", help="Remove an imported provider schema, refusing while a project pins it."
    )
    remove_cmd.add_argument("spec", metavar="kind@version", help="The import to remove, such as foo@1.2.3.")
    remove_cmd.add_argument(
        "--force",
        action="store_true",
        help="Remove it even while a project pins that kind at that version.",
    )
    args = parser.parse_args(argv)

    if args.command == "provider":
        return _provider(args)

    # Pass flags straight through; run_server resolves env/defaults for any None.
    # (Flags absent -> None -> env var or default still applies.)
    run_server(
        host=args.host,
        port=args.port,
        open_browser=False if args.no_browser else None,
    )
    return None


if __name__ == "__main__":
    sys.exit(main())
