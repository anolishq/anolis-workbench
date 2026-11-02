# Contributing

This repo is intentionally small and strict: keep changes focused, keep checks green, and avoid unrelated refactors.

## Prerequisites

- Python 3.12+
- `uv`
- Node.js 20+
- npm

## Local Setup

```sh
uv sync --locked --extra dev
cd frontend
npm ci
```

## Run Locally

Backend server:

```sh
uv run anolis-workbench
```

Frontend dev server:

```sh
cd frontend
npm run dev
```

## Required Checks

Backend:

```sh
uv run ruff check . --output-format=github
uv run ruff format --check .
uv run mypy anolis_workbench tests
uv run pytest tests/ -v --tb=short
uv run python contracts/validate-composer-control-openapi.py
uv run python contracts/validate-workbench-api-openapi.py
```

Frontend:

```sh
cd frontend
npm run format:check
npm run lint
npm run check
npm run test:unit:coverage
npm run test:components:coverage
npm run build
```

## Contract Drift Failures

CI includes a `contract-drift` job that diffs bundled snapshots against upstream `anolis`.

If `contract-drift` fails:

1. Refresh snapshots from upstream:
   - `anolis_workbench/schemas/machine-profile.schema.json`
   - `anolis_workbench/schemas/runtime-config.schema.json`
   - `contracts/runtime-http.openapi.v0.yaml`
2. Re-run local validators:
   - `uv run python contracts/validate-composer-control-openapi.py`
   - `uv run python contracts/validate-workbench-api-openapi.py`
3. Include the snapshot refresh and validator pass in the same PR.

## External contributions

We welcome contributions from people using the project or genuinely interested
in becoming involved.

Before implementing changes, prospective contributors should engage through an
issue or discussion, explain the problem or proposed improvement, and coordinate
with maintainers.

Open issues are not automatically invitations to submit implementations.
Unsolicited pull requests from accounts without prior project involvement or
maintainer coordination may be closed without review.

Automated contribution farming, mass-submitted PRs, and superficial changes
intended primarily to build contribution history are not accepted. AI-assisted
contributions are welcome when contributors understand, validate, and take
responsibility for their work.
