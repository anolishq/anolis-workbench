# Anolis System Composer

A graphical tool for composing, configuring, and launching Anolis systems.

## Quick Start (Windows)

Install Python dependencies first from repo root:

```sh
python -m pip install -r requirements.txt
python -m pip install -e .
```

Double-click `system-composer/start.cmd`. A browser window will open
automatically at `http://localhost:3002`.

## Quick Start (Linux / macOS)

```sh
./system-composer/start.sh
```

Optional environment overrides:

1. `ANOLIS_COMPOSER_HOST` (default: `127.0.0.1`)
2. `ANOLIS_COMPOSER_PORT` (default: `3002`)
3. `ANOLIS_OPERATOR_UI_BASE` (default: `http://localhost:3000`)
4. `ANOLIS_COMPOSER_OPEN_BROWSER` (`1` or `0`, default: `1`)
5. `ANOLIS_DATA_DIR` (project storage root, default: `~/.anolis/systems`)

## Save and Validation Semantics

Save is backend-authoritative.

1. The frontend submits the full `system.json` payload.
2. The backend validates schema first (`system-composer/schema/system.schema.json`).
3. The backend then runs semantic validation (`system-composer/backend/validator.py`).
4. If either stage fails, save is rejected with HTTP 400 and structured error details.
5. YAML render output is written only after validation passes.

## Contract Dependencies

1. Runtime config schema: `schemas/runtime-config.schema.json`
2. Runtime HTTP OpenAPI snapshot: `contracts/runtime-http.openapi.v0.yaml`
3. Composer control OpenAPI: `contracts/composer-control.openapi.v1.yaml`
4. System schema: `system-composer/schema/system.schema.json`
5. Provider catalog: `system-composer/catalog/providers.json`
