# Compatibility matrices: provisioning vs conformance

The org maintains **two** version matrices that look similar but answer
different questions. This note records how they relate so the shared dimension
doesn't silently drift. (See anolishq/anolis-workbench#113.)

## The two matrices

| | Workbench **provisioning** matrix | Org **ADPP conformance** matrix (0F) |
| --- | --- | --- |
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

## How the matrices are linked (warn-first)

The link from 0F's verdict to the workbench bump is implemented (epic
anolishq/anolis-workbench#137), in two parts:

- **Durable publish (anolishq/.github#101).** The 0F ADPP matrix commits its
  verdict to the `adpp-compat-data` branch of `anolishq/.github` as
  `data/adpp-compat/latest.json` on every run — a stable location, replacing the
  ephemeral 90-day artifact.
- **Consume in the bump (#155).** When `check-compat-matrix.yml` opens a bump PR,
  it fetches `latest.json` and **warns** if the bump pins a `(provider, version)`
  pair 0F marks **❌ `fail`**: it adds the `0f-incompat` label and a PR comment
  listing the failing protocol(s). This is a **warning, not a block** — the bump
  PR is already human-reviewed, and the reviewer decides.

Matching rules:

- A workbench provider id equals 0F's provider short-name; versions compare with
  the leading `v` stripped (0F tags are `v`-prefixed).
- Only `fail` is flagged. `error`/⚠️ (the harness produced no verdict — e.g. a
  version predates a protocol) and unmeasured pairs are **not** flagged.
- The fetch is best-effort: if `latest.json` is unreachable, the bump proceeds
  without a warning.

**Not yet mapped:** workbench pins a `runtime` (anolis), while 0F's other axis is
the `protocol` harness version (runtime ≠ protocol). Until that mapping is
defined, the warning fires for **any** protocol a pinned provider version fails,
naming the protocol so the reviewer can judge relevance. Tightening this (and an
optional escalation from warn to block) can come later.
