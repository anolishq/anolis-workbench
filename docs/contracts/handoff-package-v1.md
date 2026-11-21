# Handoff Package v1

Status: Active baseline.

## Purpose

Define the deterministic package format produced by commissioning as a
provenance record of the commissioned machine; it is not a deploy artifact.

## Distribution Form

1. Archive format: zip
2. File extension: `.anpkg`
3. Canonical internal layout:
   - `machine-profile.yaml`
   - `runtime/anolis-runtime.yaml`
   - `runtime/behaviors/*` (each behavior the profile declares, and the one the exported variant references)
   - `providers/<provider-id>.yaml`
   - `meta/provenance.json`
   - `meta/checksums.sha256`

## Manifest Lock

`machine-profile.yaml` remains the package manifest root (`schema_version: 1`).
No competing manifest dialect is introduced.

Path rewrite contract at export:

1. `runtime_profiles` -> `{manual: runtime/anolis-runtime.yaml}`. Only the
   `manual` variant is exported; other variants are omitted and listed in
   `meta/provenance.json.omitted_variants`.
2. `providers.<id>.config` -> `providers/<id>.yaml`.
3. `behaviors[]` -> `runtime/behaviors/<file>.xml`. Every declared entry is
   carried, together with the exported variant's `behavior_tree`, and the
   packaged `behaviors[]` lists them all. Two declared files with the same
   file name fail the export rather than overwrite each other.
4. Runtime provider `--config` args -> `providers/<id>.yaml`.

Provider commands are not rewritten. Each provider's `command` is carried as
the canonical deploy token,
`../anolis-provider-<kind>/build/<preset>/anolis-provider-<kind>`, which
`install.sh` rewrites to the installed binary at deploy time. The package
carries no binaries and is not meant to run: it is a provenance record, and
the deployable is the staged bundle from `install.sh --stage`.

## Determinism Lock

For identical project input state, exported bytes are identical across wrappers
(Workbench HTTP export and the `anolis-package` CLI):

1. Stable file ordering in archive.
2. Stable zip metadata.
3. Deterministic `meta/provenance.json.exported_at`:
   - derived from `meta.created` in the project's `workbench.json` sidecar
     (UTC second precision), or
   - fallback `1970-01-01T00:00:00Z` when absent/unparseable.

## Security and Secret Policy

1. `telemetry.influxdb.token` is stripped from packaged runtime config content.
2. Deploy-time token injection uses `INFLUXDB_TOKEN`.
3. Validator fails if token-like secrets remain in package files.
4. `meta/provenance.json` records redaction policy metadata.

## Integrity Policy

`meta/checksums.sha256` contains sha256 lines (`<digest>  <relative-path>`) for
every package file except `meta/checksums.sha256` itself.

## Validation Contract

Use, where `<package-path>` is an `.anpkg` archive or an extracted package
directory:

```bash
anolis-validate <package-path>
```

Optional runtime config check:

```bash
anolis-validate <package-path> --runtime-bin <runtime-binary>
```

Validation includes:

1. structure + checksums
2. machine-profile schema and package-context references
3. runtime schema compatibility
4. references resolve inside the package root, and each provider `command` is
   a canonical deploy token
5. optional runtime `--check-config` on the package's runtime config. This
   checks the config only; it starts no provider, and cannot, since the
   commands are deploy tokens.

## Related Artifacts

1. `anolis_workbench/core/exporter.py`
2. `anolis_workbench/core/package_validator.py`
3. `anolis_workbench/cli/package_cli.py`
4. `docs/contracts/handoff-package-baseline.md`
