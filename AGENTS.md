# AGENTS.md — anolis-workbench

> Per-repo conventions for coding agents (Claude Code, OpenCode, …). The
> canonical cross-repo rules — Conventional Commits, minimal-first/YAGNI, no
> secrets, run checks before asserting success — live in the user's **global**
> `AGENTS.md` and are not repeated here. This file records only what is
> **specific to this repo**: the commands, the gate, and the non-obvious things
> agents get wrong here.

## What this is

The commissioning / provisioning workbench: the product is guided machine
**commissioning + provisioning** flows and the **handoff packages** they emit.
It's a full-stack monorepo:

- **Python backend** (`anolis_workbench/`, packaged as a wheel via `uv`).
- **SvelteKit frontend** (`frontend/`) — built into a SPA that is bundled
  **into the Python wheel**.
- **Tauri (Rust) desktop wrapper** (`desktop/src-tauri/`) — bundles the frozen
  Python server as a sidecar.

## Build / test

- Use the **`justfile`**: `just setup`, `just fmt`, `just lint`, `just check`,
  `just test`, `just build`. `just check` is the CI-equivalent gate (Python
  ruff/mypy + frontend lint/check + Rust fmt/clippy/check).
- The required CI status check is the **`ok`** job (it aggregates every lane);
  never bypass it, and never merge red.

## Repo-specific gotchas

- **Rust/desktop has a PR-time CI lane** (`cargo fmt --check` / `clippy` /
  `check`). `tauri-build` asserts that every `externalBin` —
  `binaries/anolis-workbench-sidecar-<host-triple>` — exists, even under
  `cargo check`/`clippy`. The real frozen sidecar is produced only by the
  release freeze, so the lint lane **stages a zero-byte placeholder** to
  satisfy the existence check. Keep that placeholder step when touching the
  desktop lane; it is never bundled or run.
- **Deployment is delegated to the anolis repo's `install.sh`** (#161):
  `core/deploy.py` materializes a project config dir and runs
  `install.sh --project` (local/remote) or `--stage` (offline bundle). There is
  no workbench-side compatibility matrix, bundler, or runtime systemd renderer
  — component versions come from the machine-profile `components:` section
  (resolved from latest releases at materialize time).
- Shared `.github` actions/workflows are SHA-pinned with a `# <tag>` comment so
  Renovate can track them — keep the comment when bumping.

## Backlog

Backlog lives in **GitHub issues, not a `TODO.md`**. There is no in-repo TODO
file; file work as issues in `anolishq/anolis-workbench`.
