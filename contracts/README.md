# Contracts

Repository-level contract snapshots and validators.

## Runtime API

1. `runtime-http.openapi.v0.yaml`
2. Structural + example validation remains in the runtime repository (`anolis`).

## Composer Control API

1. `composer-control.openapi.v1.yaml`
2. Validator: `python3 contracts/validate-composer-control-openapi.py`
3. Baseline: `../docs/contracts/composer-control-baseline.md`

## Handoff Package

1. Validator: `python3 contracts/validate-handoff-packages.py <package.anpkg>`
2. Baseline: `../docs/contracts/handoff-package-baseline.md`
3. v1 format spec: `../docs/contracts/handoff-package-v1.md`
