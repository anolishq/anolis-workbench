"""CLI entry point for the Anolis Workbench HTTP server."""

from __future__ import annotations

import argparse
import importlib.metadata

from anolis_workbench.server.app import main as run_server


def main() -> None:
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
    args = parser.parse_args()

    # Pass flags straight through; run_server resolves env/defaults for any None.
    # (Flags absent -> None -> env var or default still applies.)
    run_server(
        host=args.host,
        port=args.port,
        open_browser=False if args.no_browser else None,
    )


if __name__ == "__main__":
    main()
