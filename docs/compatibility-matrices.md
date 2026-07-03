# Compatibility matrices: provisioning vs conformance

Historical note (see anolishq/anolis-workbench#113): the org used to maintain
**two** version matrices that looked similar but answered different questions —
the workbench **provisioning** matrix
(`anolis_workbench/schemas/compatibility-matrix.yaml`, "what binary versions do
we install together?") and the org **ADPP conformance** matrix (0F, in
`anolishq/.github`, "which released (protocol × provider) pairs conform?").

## Status

The provisioning matrix is **being retired** (#166): the platform is
config-driven, and a deployment's `machine-profile.yaml` `components:` section
is the version source — Renovate bumps versions there directly. Accordingly:

- The weekly bump poller (`check-compat-matrix.yml`) and its 0F warn step
  (#155) are **gone**.
- The matrix file itself is deleted by the install.sh delegation work (#161);
  until then it only gates the release version sync (`workbench_version` ==
  `pyproject.toml`).

## What remains

The org 0F conformance matrix is unchanged and still publishes its **durable
verdict** (anolishq/.github#101) to the `adpp-compat-data` branch of
`anolishq/.github` as `data/adpp-compat/latest.json`. That verdict is a
**lookup** — "is provider X at version Y conformant with protocol Z?" —
consulted when a deployment config picks a version, not a gate needing its own
version catalog.
