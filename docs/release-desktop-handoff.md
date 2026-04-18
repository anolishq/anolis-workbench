# Desktop Release Handoff (Phase 16)

Audience: maintainers preparing native installer bundles for `anolis-workbench`.

## Overview

Desktop packaging is split into two explicit layers:

1. **Python sidecar freeze** (`scripts/freeze_server.py`) -> onefile executable.
2. **Tauri bundle build** (`desktop/src-tauri`) -> platform installers.

The desktop wrapper is a shell only: frontend still talks directly to
`http://127.0.0.1:3010` over localhost HTTP/SSE.

## Required Inputs

1. Version in `pyproject.toml` (must match workflow input).
2. Built frontend assets at `anolis_workbench/frontend/dist/`.
3. Rust + Node toolchains available in CI runners.

Version alignment rule:

- `desktop-release.yml` validates that the requested version exactly matches all of:
  - `pyproject.toml` (`project.version`)
  - `desktop/package.json` (`version`)
  - `desktop/src-tauri/Cargo.toml` (`package.version`)
  - `desktop/src-tauri/tauri.conf.json` (`version`)

## Workflow

Use:

- `.github/workflows/desktop-release.yml`

Job flow:

1. Validate semver + cross-file version alignment.
2. Freeze sidecar on Linux + Windows.
3. Stage sidecar into Tauri `externalBin` path.
4. Build installers per target.
5. Generate CycloneDX SBOM artifacts.
6. Attach installers + SBOMs to GitHub Release tag.

## Sidecar Freeze Guards

`freeze_server.py` enforces:

1. Frontend dist presence check.
2. Minimum executable size threshold check.
3. Executable smoke checks (`--help`, `--version`) unless explicitly skipped.

## Manual Operations

1. Verify desktop app identity in `desktop/src-tauri/tauri.conf.json`:
   - `identifier: org.feastorg.anolis-workbench`
   - `productName: Anolis Workbench`
2. Confirm port `3010` is documented as reserved in user-facing release notes.
3. Confirm release assets include at minimum:
   - Windows `.msi`
   - Linux `.AppImage` and/or `.deb`
   - CycloneDX SBOM JSON files

## Notes

1. macOS packaging remains a stretch target and is intentionally not hard-gated.
2. Desktop release is intentionally separate from PyPI publish to keep failure
   domains clear (installer build dependencies vs Python package publish path).
