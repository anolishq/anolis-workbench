# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Historical note:

- This changelog was reconstructed from git history on 2026-04-19.
- `0.1.0`, `0.1.1`, and `0.1.2` were inferred from version-bump commits.
- `0.1.3` is the first tagged release (`v0.1.3`).

## [Unreleased]

### Changed

- Frontend formatting cleanup after the `0.2.0` baseline (`4cec7b8`).

## [0.2.0] - 2026-04-19

### Added

- Browser smoke contract tests and frontend bundle-size reporting (`c8b9345`).
- Desktop Tauri wrapper scaffold and desktop release pipeline foundation (`6adddd7`).
- Component testing lane with Vitest + Testing Library for Home/Compose/Commission, coverage wiring, and Firefox Playwright smoke target (`8b4a94d`).
- Lightweight contributor/release docs and uv-first setup guidance (`e635d2d`).

### Changed

- Finalized cutover from legacy composer assets/tests; normalized docs and runtime/control port validation (`dda1ac4`).
- Completed frontend TypeScript migration and hardening, including contract-backed types and stricter linting (`c1baca5`, `1d1373e`, `e5a83b8`).
- Hardened frontend behavior by replacing `window.confirm`, moving defaults to `/api/config`, and removing raw component `fetch`/localhost assumptions (`6522ee4`, `682fce3`).
- Established formatting and linting standards with `.editorconfig`, Prettier, and ESLint flat config (`7aeaf03`, `9f30a9d`, `d73852d`, `e24c8e1`, `77b241a`, `deb5735`).

### CI

- Added Dependabot for npm + GitHub Actions and enabled rebase strategy (`fca946b`, `8f2ba43`).
- Hardened frontend CI with audit, svelte-check, ESLint, Playwright retry policy, and component coverage (`da2f9f7`, `c1d8aa5`, `1ec0a5e`, `1d0e2dd`, `8b4a94d`).
- Standardized Python CI on `uv sync --locked` with dev extras and pinned release jobs to Ubuntu 24.04 (`e0c5e94`, `c9cc89e`, `e3fa2c2`).
- Applied dependency upgrades via Dependabot across GitHub Actions and frontend dev dependencies (`2e24c04`, `ef22a2b`, `10f842f`, `91c4815`, `0b484b1`, `bc9b22c`, `bb5d580`, `076ff94`, `c247322`, merged via `1b33f66`, `c1f7119`, `301f5ee`, `554e606`, `a6e4b7f`, `b5b7021`, `93de2a1`, `eb62b6e`, `51c0fe9`).

### Docs

- Added explicit versioning policy (`9c9ccdf`).

### Version

- Bumped project and desktop lockstep versions from `0.1.3` to `0.2.0` (`2993f68`).

## [0.1.3] - 2026-04-18

### Changed

- Added `argparse`-based CLI `--version` handling and tightened smoke timeout behavior (`44aa1de`).

### Release

- Tagged as `v0.1.3` (tag points to `44aa1de`).

## [0.1.2] - 2026-04-18

### Changed

- Improved smoke-test observability with verbose output and pip timeout handling (`523760a`).

## [0.1.1] - 2026-04-18

### Fixed

- Ensured built frontend `dist/` is packaged in wheel artifacts (`367983e`).
- Added exponential backoff for PyPI propagation during smoke install (`a34f76c`).
- Allowed release workflow reruns by skipping already-existing PyPI artifacts (`3cb5536`).

## [0.1.0] - 2026-04-18 (inferred)

### Added

- Initial extraction of `anolis-workbench` from monorepo and repository normalization (`77304b6`, `72af0f8`, `424e4a1`, `d4693e8`, `ed4212e`, `40579a2`).
- Workbench API OpenAPI v1 contract, validator, and CI wiring (`01548b2`).
- Unified `anolis_workbench` package structure replacing split composer/workbench layout (`aedc9b8`).
- Python packaging and publish foundation with hatchling, uv, and release/smoke workflows (`9303832`).
- Svelte 5 + Vite frontend scaffold and full SPA implementation (`ac3a2b6`, `96a9e07`).

### Fixed

- Early CI correctness and portability fixes for mypy/ruff, Linux `ctypes.windll`, and workflow/frontend build ordering (`59c5d1a`, `480f76e`, `97c01aa`, `8fc1cea`, `8fcb9e6`, `3c61c84`, `4a599dc`).

