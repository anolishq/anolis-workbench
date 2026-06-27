# Compatibility matrices: provisioning vs conformance

The org maintains **two** version matrices that look similar but answer
different questions. This note records how they relate so the shared dimension
doesn't silently drift. (See anolishq/anolis-workbench#113.)

## The two matrices

| | Workbench **provisioning** matrix | Org **ADPP conformance** matrix (0F) |
|---|---|---|
| Where | `anolis_workbench/schemas/compatibility-matrix.yaml` | `anolishq/.github` `adpp-compat-matrix.yml` |
| Bumped by | `.github/workflows/check-compat-matrix.yml` (weekly, Mon 09:00 UTC) | scheduled run (Sun 05:00 UTC), report-only |
| Question | "What runtime + provider **binary** versions do we ship/install together?" | "Which released (protocol × provider) pairs still **conform**?" |
| Gates | the workbench release | nothing — report-only (`$GITHUB_STEP_SUMMARY` + a `results.json` artifact) |

They are intentionally **separate concerns** — provisioning is operational,
conformance is verification — and should stay separate.

## The shared seam

Both independently discover "the current released set" (each polls
`releases/latest`). That duplicate discovery is the drift seam: a release ripples
to each on its own schedule, so the two can momentarily disagree, and nothing
today links "versions workbench provisions" to "pairs 0F verified as ✅".

## Ripple rules (the intended behavior)

- **New provider release** → the workbench bump PR fires (weekly poller) **and**
  0F auto-includes it on its next run.
- **New protocol release** → 0F's axis grows; the workbench *runtime* pin may
  need a bump.
- A 0F **❌** for a pair workbench pins → should eventually warn/block that pin.

## Why there's no automation yet (deferred)

Wiring 0F's verdict into the workbench bump (so a ❌ pair warns/blocks) would
require 0F to publish its verdict to a **stable** location first — today it's
only an ephemeral workflow artifact. The original gates for this work
(anolishq/anolis-workbench#113 and the 0G provider-SDK readiness review
anolishq/anolis-protocol#29) are now both **closed** and the provider-SDK
extraction is complete, so this is unblocked; it is tracked in
anolishq/anolis-workbench#137. Until it lands the two matrices stay separate and
this document is the source of truth for their relationship.
