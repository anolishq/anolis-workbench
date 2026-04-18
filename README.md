# anolis-workbench

Commissioning shell repository for Anolis.

Phase 13 structure:

1. `anolis_workbench/core` — pure domain library (projects, renderer, launcher, exporter, validators, paths)
2. `anolis_workbench/server` — unified HTTP server (Compose + Commission + Operate `/v0/*` proxy)
3. `anolis_workbench/cli` — CLI entry points (`anolis-workbench`, `anolis-package`)

## Install

```sh
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Run

```sh
./start.sh
```

Or run via module/entry point:

```sh
python -m anolis_workbench.server.app
# or
anolis-workbench
```

Windows launcher: `start.cmd`.

## CLI

Build handoff package:

```sh
anolis-package <project-name> [output.anpkg]
```

Validate handoff package:

```sh
python contracts/validate-handoff-packages.py <package.anpkg>
```

## Repository Contracts

1. Runtime HTTP snapshot: `contracts/runtime-http.openapi.v0.yaml`
2. Workbench API OpenAPI: `contracts/workbench-api.openapi.v1.yaml`
3. Handoff/composer baselines: `docs/contracts/*.md`
4. Runtime config schema: `anolis_workbench/schemas/runtime-config.schema.json`
5. Machine profile schema: `anolis_workbench/schemas/machine-profile.schema.json`


## Handoff Docs

1. Commissioning handoff runbook: `docs/commissioning-handoff-runbook.md`
2. Handoff package v1 format: `docs/contracts/handoff-package-v1.md`
