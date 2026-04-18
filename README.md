# anolis-workbench

Extracted commissioning shell repository for Anolis.

This repo ships both backends as one distribution:

1. `anolis_workbench_backend` (Compose/Commission/Operate shell + handoff export)
2. `anolis_composer_backend` (project CRUD, render, preflight, launch)

## Install

```sh
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Run

Workbench:

```sh
./workbench/start.sh
```

System Composer:

```sh
./system-composer/start.sh
```

Windows launchers are `workbench/start.cmd` and `system-composer/start.cmd`.

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
2. Composer control OpenAPI: `contracts/composer-control.openapi.v1.yaml`
3. Handoff/composer baselines: `docs/contracts/*.md`
4. Runtime config schema: `schemas/runtime-config.schema.json`
5. Machine profile schema: `schemas/machine-profile.schema.json`


## Handoff Docs

1. Commissioning handoff runbook: `docs/commissioning-handoff-runbook.md`
2. Handoff package v1 format: `docs/contracts/handoff-package-v1.md`
