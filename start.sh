#!/usr/bin/env bash
# Run the workbench from a source checkout, in the project's uv environment.
# Set it up first: see "Build from source" in README.md.
set -euo pipefail
cd "$(dirname "$0")"
exec uv run anolis-workbench "$@"
