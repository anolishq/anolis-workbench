# Desktop Wrapper (Phase 16)

This directory contains the native Tauri wrapper for `anolis-workbench`.

## Runtime Model

1. Tauri hosts a native WebView window.
2. On app startup, Rust launches the packaged Python sidecar.
3. The sidecar serves the workbench at `http://127.0.0.1:3010`.
4. Frontend traffic stays on localhost HTTP/SSE exactly like browser mode.

## Development

Prerequisites:

1. Rust toolchain
2. Node 20+
3. Python sidecar available (local Python environment for `anolis-workbench`)

Run:

```bash
cd desktop
npm install
npm run tauri:dev
```

## Build

The release workflow stages a frozen sidecar binary into:

- `desktop/src-tauri/binaries/anolis-workbench-sidecar-<target>[.exe]`

Then runs:

```bash
cd desktop
npm run tauri:build -- --target <rust-target-triple>
```

Installer targets (phase baseline):

1. Windows `.msi`
2. Linux `.AppImage` and `.deb`

Versioning note:

- Desktop metadata versions in `desktop/package.json`,
  `desktop/src-tauri/Cargo.toml`, and `desktop/src-tauri/tauri.conf.json` are
  intentionally kept in lockstep with `pyproject.toml`.

Port note:

- `3010` is reserved by the desktop wrapper; if already occupied, startup fails
  with a native error dialog.
