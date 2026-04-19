# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0]

### Added

- Component tests for `Home`, `Compose`, and `Commission`.

### Changed

- CI now runs frontend component coverage in addition to unit coverage.
- Playwright browser install + config now include Firefox smoke coverage.

## [0.1.3]

### Added

- Native desktop wrapper (`desktop/`) with sidecar-based runtime model.
- Desktop release handoff and version-lockstep documentation.

### Changed

- CI and release hardening around version validation and quality gates.

## [0.1.2]

### Added

- SPA frontend with Compose, Commission, and Operate workflows.
- Runtime `/v0/*` proxy support in the Python server layer.

### Changed

- Project shell and navigation model aligned to commissioning workflow.

## [0.1.1]

### Added

- Handoff package tooling (`anolis-package`, `anolis-validate`).
- OpenAPI/schema validation utilities and contract baseline docs.

### Changed

- Repository contracts and docs standardized for commissioning handoff.

## [0.1.0]

### Added

- Initial `anolis-workbench` commissioning shell repository.
- Core domain library + HTTP server + CLI entry point foundation.
