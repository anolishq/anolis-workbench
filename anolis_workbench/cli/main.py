"""CLI entry point for the Anolis Workbench HTTP server."""

from __future__ import annotations

from anolis_workbench.server.app import main as run_server


def main() -> None:
    run_server()


if __name__ == "__main__":
    main()
