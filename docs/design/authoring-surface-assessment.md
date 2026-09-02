# The authoring surface: assessment and design record

**Status:** decided. Filed as epic #337 on 2026-09-02.
**Assessed against:** `anolis-workbench` v0.14.0, with `anolis` v0.1.40 as the upstream reference.

This is the evidence base behind #337. It records the decision inventory, the
journey maps, the designs considered, and — most importantly — **the designs
rejected and why**, so those arguments do not have to be had twice.

Where this document and #337 disagree, #337 wins: it carries the decisions as
actually taken.

## What was decided

| Question | Decision |
| --- | --- |
| Epic scope | Authoring surface only. The install leg stays with #232. |
| A guided wizard? | Yes, but last, and only steps 1–3 (authoring). #344. |
| PrusaSlicer-style tiers? | **No.** Difficulty is the wrong axis; decidability is the right one. A depth control that is a floor, not a filter. #340. |
| Field tiering mechanism | Structural rules over provider-owned schema signals, plus `x-anolis-consequence` upstream. Never a workbench-side table. |
| Cross-repo | runtime-config resync on the critical path (#345, anolishq/anolis#293); the SDK annotation filed but not gating (anolis-provider-sdk#30). |
| Safe state | Read-only reporting surface now (#346); the live editor deferred. |
| The three tabs | Kept. Revisit once the readiness panel (#342) shows whether they are the problem. |

---

## How this was produced

A 15-agent workflow over the checked-out repos: five parallel readers mapping the
product (every decision it asks for, five end-to-end journeys, the safety/honesty
floor, reusable machinery + field-tiering mechanisms, and backlog reconciliation),
then three independent full designs from deliberately opposed angles, each attacked
by two adversarial critics, then a synthesis that picked a winner on the merits and
grafted the rest.

The three designs were: **wizard-primary** (your framing at face value),
**progressive-disclosure** (the PrusaSlicer model), and **task-oriented** (which
argues the tabs are the actual bug). The third existed specifically because you said
not to let your framing bias the work.

Every load-bearing claim below has a `file:line`. I re-verified nine of the sharpest
ones myself; see [Corrections](#corrections-and-caveats) for the one that was wrong.

---

## The headline verdict

**Your diagnosis is right. Both of your proposed shapes are partly wrong.**

### "The workbench is complex and terse" — correct, and worse than you framed it

The inventory found **112 distinct decisions** across the product:

| Classification | Count |
| --- | --- |
| `must-ask` — genuinely machine-specific, no sane default | 34 |
| `safe-default` — a default is right for nearly everyone | 35 |
| `expert-only` — real but rare | 31 |
| `should-not-exist` — should be derived, or is a leaked implementation detail | 12 |

**77 of the 112 are in Compose alone**, on one unsegmented scroll. A first-run user
faces roughly 45–55 controls before the first Save, of which about 8 are genuinely
must-ask.

The ratio is the problem, not the count. `hardware.bus_path` — the single string
separating a config that simulates from one that drives a pump — renders at the same
visual weight as InfluxDB batch size and CORS credentials.

And **"terse" is the more accurate half of your complaint**: the tool already writes
the correct value into most of these fields and then asks you to confirm it.

### A guided wizard: right instinct, wrong first deliverable, and wrong as a shell

All three designers independently concluded in their own final paragraphs that the
wizard should ship *last*. Six independent critics agreed. The reason was consistent:
**every wizard step failed not because navigation was wrong, but because the product
lacks a capability underneath it.**

- The only shipped template points `host_paths` at a developer's CMake tree
  (`templates/sim-quickstart/workbench.json` → `build/dev-release/core/anolis-runtime`
  — verified), so preflight fails on any non-developer machine, and no control in the
  product can produce those binaries.
- `/api/status` is local PID discovery over `running.json`, so a systemd-supervised Pi
  reads as "not running" forever.
- `POST /api/provision/cancel` reports success while `job._cancel` has **zero readers
  anywhere in the codebase** (verified: two hits total, the declaration at
  `provision.py:36` and the write at `:246`) — `install.sh` keeps running as root.
- The deploy step ends in an ssh session.

A wizard laid over that is a *more convincing* failure than the current terse form.
Today the UI fails ugly and you correctly conclude the tool is unfinished. A wizard
gives the same dead end a competent, reassuring frame.

Two further structural facts:

1. **It serves one journey, once per machine.** The daily loop — edit a device
   address, push it to a rig in the field — has no wizard entry point and, today,
   no working UI at all.
2. **Your product already contains the proof.** `Onboarding.svelte` *is* a wizard.
   See the next section.

### PrusaSlicer-style complexity tiers: right mechanism, wrong axis, and the framing leads somewhere dangerous

PrusaSlicer's Simple mode is coherent because **a working print exists at every
tier** — the defaults are a complete answer.

Here there is no simple answer to "which I2C bus is the board on" or "which address
is each device at". A beginner commissioning a bread rig *must* set `bus_path`
(required, no default — verified in the envelope) and every device address; no tier
can spare them. An expert on the sim template needs to set nothing.

**Difficulty does not track the axis. Decidability does** — can the workbench predict
the value that lands in the artifact, and is being wrong about it a physical or
exposure event?

The framing is actively dangerous in two specific ways:

1. **"Which provider fields are beginner-level" has no legal home in this repo.** A
   workbench-side table is exactly the leak epic #289 spent two epics deleting, and
   it rots the first time bread renames a field.
2. **A *difficulty* label invites a provider author to mark
   `command_watchdog_ms` as advanced.** That is the firmware command watchdog on a
   heater or motor. Verified in the bread envelope: `{"type": "integer", "default": 0,
   "description": "Firmware command-watchdog timeout; 0 never arms."}` — currently
   rendered as an unremarkable number box. **A tier system that can hide it is worse
   than no tier system.**

That is why the recommended annotation is a consequence *sentence* with no hiding
semantics, not a tier value.

---

## The worst thing in the product right now

**The very first click a new user ever makes silently does nothing visible, and
permanently destroys the only guided path in the product.**

"Set up this device" (`Onboarding.svelte:88`). Four defects stack on that one click:

1. `POST /api/provision/install` returns 202 before any work happens
   (`provision.py:173-183`), so the UI has already navigated away before anything can fail.
2. It navigates to `/onboarding/progress/<job_id>`, which `parseRoute` does not match
   (`App.svelte:61-69`), so `navigateTo` silently rewrites history to `/` and drops
   the user on a Home screen reading "No projects yet."
3. The job's error is unreachable twice over — the frontend's own
   `catch (err) { starting = false; }` (`Onboarding.svelte:41-43`) discards it without
   reading it, and the SSE stream at `/api/provision/status/<job_id>` that carries
   every stage and the final error **has no consumer anywhere in the frontend** (the
   only `EventSource` on it, `Commission.svelte:401`, is for bundles).
4. Meanwhile the background thread creates `~/.anolis/systems/sim-quickstart` **before**
   running install.sh (`provision.py:76-79`), which flips `first_run` to false
   (`onboarding.py:37`) **forever** — so a failed attempt burns the onboarding screen,
   and with it the only entry point to remote provisioning anywhere in the UI.

The underlying `sudo bash install.sh` then runs with no tty, no `-S`, no askpass and
captured output (`executor.py:108-119`), so it can block for the full 1800s timeout
against an invisible password prompt.

Net: a commissioning tool for machines with real actuators greets a new user with a
button that appears inert, may be hung for half an hour, has already consumed itself,
and has thrown away the one sentence that would have explained why.

This is issue #228, and it is worth reading that issue's existing design notes before
anyone rewrites the file.

### Journey health, for context

| Journey | Steps | Breaks | Blockers |
| --- | --- | --- | --- |
| 1. Fresh install → running machine on this host (guided arm) | 1 | 8 | 4 |
| 1b. Same, fallback arm (Skip → Create Project) | 5 | 6 | 2 |
| 2. Fresh install → remote Raspberry Pi over SSH | 3 | 6 | 4 |
| 3. Import `bioreactor-v1` → deployed and running | 5 | 7 | 3 |
| 4. Edit an existing project, redeploy to a field machine | 5 | 7 | 2 |
| 5. Day-2: operate a machine someone else commissioned | 2 | 7 | 4 |

---

## The recommendation

**Build one authoring surface with a depth control, plus a readiness panel, plus a
hard-fix list — and defer the guided path behind six capability gates.**

The winner was **"One Surface, Three Depths"**, chosen not because it scored highest
but because **its fatal flaws are removable without killing its thesis, and the other
two's are not.**

- *Wizard-primary* had the best instincts and the highest architecture score, but
  every fatal flaw was load-bearing. Its device inventory requires prefilling
  `discovery.mode: scan`, which write-probes 0x08–0x77 on a shared bus **and**
  renders `_i2c_conflict_errors` vacuous (no authored `devices[]` to walk). Its
  headline win — "nobody types a CMake build-tree path" — does not fire, because
  `_point_host_paths_at_prefix` is only reached via `provision_project`, which
  `_prepare_workspace` skips whenever the project dir exists, which its own step 4
  guarantees. Strip the unsound parts and the thesis is gone.
- *Task-oriented* had the highest honesty score of any submission and the
  best-reasoned architecture, but its thesis is a shell rewrite; both its critics and
  its own author said the shell rewrite should not ship. It also conflicts textually
  with open PR #319 on the 1222-line `Operate.svelte`.

### 1. The depth control

Three positions in the topbar (**Essential / Tuning / Everything**), default
Essential, persisted per-user in `localStorage`, **never in a project artifact**.

What it does is deliberately narrower than what you asked for: **it sets the default
expansion state of collapsible groups.** It cannot hide a field that is
required-without-default, currently invalid, named by a save error, diverging from
its default, or safety-pinned. When overrides force fields open it reads
`Essential +3` with a tooltip naming why.

That redefinition is not timidity. `canonical_validator` returns `{path, message}` and
`Compose.svelte:186-192` renders save errors by path — so a true global filter can
hide a field named by a refusal the user then has no control to act on.

### 2. Collapse by decidability, and print what you collapse

A scalar renders **expanded** if it is required with no default, has no default and no
const, holds a value diverging from its default, currently fails `validateField`
(`schema.ts:234`), or carries `x-anolis-consequence`. Otherwise it joins a group whose
header names the count **and the values**:

> ▸ 3 settings at provider defaults — query delay 10000 µs · I/O timeout 100 ms · I/O retries 2

Against the real bread envelope this collapses exactly `query_delay_us` / `timeout_ms`
/ `retry_count` and keeps `bus_path`, `discovery.mode`, `discovery.addresses` and the
whole device table — the split an engineer would draw by hand.

**Two hard implementation rules:**

- It must **not** route through `resolveConditionals().forbidden`, because
  `pruneForbidden` (`schema.ts:364-391`) **deletes keys** and runs on every write via
  `handleChanged`. Reusing it would make switching to a shallower depth silently erase
  authored commissioning data.
- **Array items never collapse** in the first slice. `command_watchdog_ms` lives at
  `devices/items/properties` and is structurally indistinguishable from
  `query_delay_us` (both: integer, defaulted, not required). Until
  `x-anolis-consequence` exists, living inside `devices[]` is its only protection.

### 3. A workbench-owned table for runtime config, guarded structurally

`RuntimeForm.svelte` is *already* a hand-authored overlay — the vendored runtime-config
schema carries no titles, no descriptions and no defaults, and root `required` is
`["providers"]` alone. Replace the 18 hand-written blocks with a table of **label +
tier + honesty note** — but **not default values**, which would be an unversioned
hand-copy of `anolis/core/runtime/config.hpp` shadowing a schema that already has a
lock file.

A collapsed row for an absent key says *"not set — the runtime's default applies"*;
when the key is present it prints the authored value.

`http.bind` is pinned always-visible (`_bind_errors` can refuse the save over it), and
`cors_allowed_origins` joins it as Essential-pinned because its upstream default is `{"*"}`.

**Guard:** a test asserting the table's key set is a subset of property paths derived
from the locked runtime-config schema, and intersects `provider_schemas.available_kinds()`
nowhere. Without that test it degrades into the #289 violation.

### 4. A readiness panel in Compose — not a rail, not a checklist

One live-recomputed list of **what currently refuses this project**, each row quoting
the refusing layer verbatim (`deploy.py:82-91`'s pinned-components refusal, install.sh's
inert-manual gate, the non-loopback-bind gate) and deep-linked to the field that clears it.

It replaces the flat `path: message` dump and the warnings replicated per provider row.

**Live refusals only.** No DONE states, no derived safety verdict of any kind. Both
critics killed the scored-checklist version for the same reason: *"pins deliberately
empty"* and *"no safe state declared, deliberately"* are indistinguishable in the
document from *"not done yet"*, and the only fixes are a permanently-red row (which
trains operators to dismiss red rows) or stored acknowledgement state (which is the
shadow the architecture forbids).

*Prerequisite:* give `canonical_validator` errors real paths instead of `projects.py`
wrapping the whole blocking set as `path: "$"`. That is contract-touching (`path` is
required in the OpenAPI) and should be its own reviewed PR.

### 5. The hard-fix list, before anything else

Preflight three-state counting; `arch` as a parameter; a real cancel or no cancel
button; SSE heartbeat + `Last-Event-ID`; `/fleet`'s server-route 404; PR #319 merged;
Issue #228's route family in **both** `App.svelte:61-69` and `_WORKSPACE_ROUTE_RE`
(`app.py:83` — verified to omit `/fleet` today); templates that describe real
machines; `host_paths` rewritten from the install result rather than seeded from a
CMake tree.

### 6. The guided path — last, and shaped as a path, not a product

Six steps, **position derived from the artifacts and the target, never stored**,
editing the same `ProjectDocument` Compose edits, ending on the readiness panel rather
than a green tick.

That derivation is the structural answer to today's worst bug: because nothing about
progress is stored, a failed attempt cannot burn the flow, a hand-authored project is
first-class, and the path is equally available on machine #7 as on machine #1.

No offline safe-state step. No automation step. No scan prefill. No skip-preflight override.

---

## The tier model, in full

**The axis is decidability, not difficulty.** A field is hideable only when the
workbench can predict the value that lands in the artifact *and* being wrong about it
is not a physical or exposure event.

### Provider config — two mechanisms, both provider-owned

**(a) Structural — ships today, zero upstream dependency.** Expand if: in `required`
with no `default`; or no `default` and no `const`; or value diverges from the schema
default; or `validateField` returns non-null; or the property carries
`x-anolis-type: i2c_address`. Otherwise collapse into a value-printing group.

Two carve-outs: `const`-valued properties are omitted entirely rather than rendered as
permanently-disabled inputs (ezo's `discovery.mode`), and array items never collapse
in slice one.

**(b) The gap structure provably cannot reach.** `hardware.query_delay_us` (integer,
default 10000, not required) and `devices[].command_watchdog_ms` (integer, default 0,
not required) are **structurally identical** in the envelope. The second is the
firmware watchdog on a heater and its default means it never arms. No
`required`/`default`/`const` walk can separate them, and no workbench-side list may.

**The annotation: `x-anolis-consequence`, a free-text sentence, not a tier.**

- *Meaning:* this property's value, **including its default**, changes what the
  machine does when something goes wrong.
- *Renderer effect:* force-visible at every depth, never collapsible, sentence renders
  beside the field. **No hiding semantics at all** — which is precisely why it is a
  sentence and not `x-anolis-tier`.
- *Owner:* `anolis-provider-sdk`. One struct member, one builder method beside
  `Field& placeholder(std::string)` at `config.hpp:166`, one emit line beside
  `x-anolis-placeholder` at `config.cpp:491`, documented with one binding rule: *a
  property whose default changes failure behaviour must carry it.*

**Contract cost, stated honestly:**

- *Per provider release: zero incremental.* The annotation rides inside the envelope
  bytes that `contracts/upstream/providers/*.lock.json` (schema_version 3) already
  sha256-hashes. No lock format change, no OpenAPI bump, no backend change —
  `workbench-api.openapi.v1.yaml:284-301` already declares envelope content
  provider-owned and unvalidated, and `compose.py:192-198` passes them byte-verbatim.
- *The real cost:* one SDK release, then one release each of **bread (0.3.8→0.3.9)**,
  **ezo (0.3.4→0.3.5)** and **sim (0.2.7→0.2.8)** before a single field is annotated.
- *Version skew is inherited free:* `provider_schemas.version_skew_warnings` already
  tells the user when the envelope they are filling in is not the version the machine pins.
- *Interim:* until the annotation lands, `command_watchdog_ms` is protected only by the
  array-item carve-out. That is weaker than pinned and it is the honest price of the
  dependency. **Do not ship array-item collapse before the annotation exists.**

### Runtime and process config — a workbench-owned table, and that is legal

Issue #289 forbids *provider-specific* knowledge. Runtime-config knowledge is not that, and
`RuntimeForm` is already the hand-authored overlay supplying every label plus three
honesty notes no generated form would produce.

- **Essential:** `http.bind` (pinned), `http.port`, `cors_allowed_origins` (pinned),
  `logging.level`
- **Tuning:** `runtime.name`, `shutdown_timeout_ms`, `startup_timeout_ms`,
  `polling.interval_ms`, `cors_allow_credentials`, `telemetry.enabled` + six Influx
  fields, three provider timeouts, four `restart_policy` fields
- **Everything:** both `host_paths` fields, the Influx token

Labels, tiers and notes — **never copies of upstream default values.**

---

## The guided flow (slice S7, for later)

| # | Step | Asks | Defaults | Escape hatch |
| --- | --- | --- | --- | --- |
| 1 | Name it, pick a template | Project name; template matching the hardware | `machine_id` derived live under the name field, **with the consequence stated at the moment of choosing** — it keys the installed directory and every config path and is readonly forever after | "Start from an existing project" → wires up `POST /api/projects/<n>/duplicate` (served at `app.py:190`, **zero frontend callers today**) — the only honest escape from an immutable `machine_id` |
| 2 | What is on the bus | `hardware.bus_path` per provider; per device type + address | Device ids auto-generate as `<type><n>`. **Nothing else.** `bus_path` never prefilled | Manual entry always available; "these aren't my devices" → full form |
| 3 | Pin the versions | `components.runtime.version` + one pin per kind any variant runs | **None, and no "resolve latest" button.** Discovery without resolution: a read-only list labelled *"listed from GitHub — nothing is written until you pick one"* | `components.*.repo` gets its first editor anywhere in the product (currently `setRuntimePin`/`setProviderPin` hardcode `anolishq`, so a fork or air-gapped mirror is unauthorable) |
| 4 | Where it will run | This computer / a machine over SSH (host, port, key) / give me a bundle. Plus target arch | No branch preselected. Arch defaults to arm64 **labelled** "Raspberry Pi (arm64)" with the alternative visible — versus `Commission.svelte:395`'s hardcoded `arch: "arm64"` with no picker and no disclosure | All three stay permanently available; nothing is one-way |
| 5 | Install, and watch it | One confirmation over a literal manifest | `--no-start` is **not** flipped on | "Give me a bundle instead" at any point |
| 6 | What this machine can and cannot promise | Nothing. It reports | Preflight as passed/skipped/failed with skips expanded; provider liveness N of M; mode reported, never advanced | Lands on the readiness panel. "Run this again" always available |

**Step 2's honesty note is the sharpest one.** `discovery.mode` must **not** be
prefilled to `scan`. bread's scan write-probes 0x08–0x77 (`crumbs_transport.cpp:23,132-136`)
on a bus the design itself lets bread and ezo share — shipped EZO addresses 0x61/0x63
are inside that range — **and** with no authored `devices[]` list,
`canonical_validator._i2c_conflict_errors` walks nothing, so the cross-provider
address-collision gate goes silent. A default change that makes an existing blocking
validator vacuous is a validator regression even though no validator code changed.

**Step 5's manifest must name:** host, project, prefix, variant, detected arch, the
exact argv, that it runs as root, that install.sh is downloaded from a GitHub release
and executed unverified (`deploy.py:240-250` plain `requests.get`, `:324-335` sudo),
that `phase_hostname` rewrites the target's hostname on ARM (`install.sh:1247-1285` —
which can invalidate the mDNS name the user just typed), that `phase_i2c` may set
`REBOOT_NEEDED`, that the systemd unit is **enabled unconditionally**
(`install.sh:776`), and — if `<prefix>/config/runtime.yaml` already exists — that
install.sh **preserves it and does not apply `--variant`** (`:575-586`), so
runtime-config edits are **not live**.

The word "preserved" appears nowhere in `frontend/src` today.

**Step 6 has no "Done — your machine is ready" banner.** The terminal state is a
truthful list.

---

## Design principles (hold a future PR against these)

1. **Hidden means defaulted, valid, and not safety-relevant.** Any one failing
   un-hides the field, in every depth. **A depth is a floor, never a filter.**
2. **A collapsed group prints the values it is deferring, not a count.** Nothing goes
   off-screen. `"4 advanced settings"` is hiding; `"4 settings at provider defaults —
   query delay 10000 µs · …"` is deferring.
3. **Depth visibility is a separate predicate from schema conditionals.** Never route
   it through `resolveConditionals`' `forbidden` set.
4. **No depth may hide a field named by a live validation error or a save error.**
5. **Provider-config tiering derives only from provider-owned signals.** A
   workbench-side table of provider field names or kinds is rejectable on sight (#289).
6. **Runtime-config tiering may live in the workbench** — but carries labels, tiers and
   notes, never copies of upstream default *values*.
7. **The workbench never computes a safety verdict.** It reports the runtime's verdict,
   or it says "not observed".
8. **No control that reaches a machine may be unlabelled about which machine.**
9. **"Done", "deployed", "ready", "safe" are permitted only where the underlying result
   distinguishes success from skipped from unobservable.** Otherwise: *"skipped: binary
   missing"*, *"consistent with safe, not observed"*, *"config on target preserved —
   your edits are NOT live"*.
10. **Any job that mutates a target must have a route that shows it** — present in both
    `parseRoute` and the server-side SPA fallback — **before the button that starts it ships.**
11. **A guided path's position is derived from the artifacts.** Nothing about progress
    is stored, including acknowledgements. Re-asking an unanswered safety question on a
    re-run is correct behaviour, not a defect.
12. **No step may offer to bypass preflight.** Check 4 *is* `canonical_validator.validate_project`
    (`launcher.py:360-367`), including the cross-provider I2C collision check that exists nowhere else.
13. **No default change may make an existing blocking validator vacuous.**
14. **Simplification defers work, explains it, or reorders it.** It never decides a value
    with a physical or exposure consequence on the user's behalf: `hardware.bus_path`,
    device addresses, `http.bind`, component pins, `automation.enabled`,
    `mode_transition_hooks` and `safety.safe_state` are never hidden and never auto-filled.

---

## Proposed slices

Vertical and independently shippable — each is useful if it ships alone and nothing
else ever does. **This is a proposal for your review, not a filing plan.**

| # | Slice | Size | Depends on | Existing issues |
| --- | --- | --- | --- | --- |
| **S1** | Stop making claims the system cannot back | S | — | part of #232; unblocks anolis#283 Ph0; merges PR #319 |
| **S2** | A started job is a job you can watch | S | S1 | closes #228; unblocks #229 |
| **S3** | Depth: collapse what is decided, print what you collapse | M | — | the authoring half of your ask |
| **S4** | One readiness panel instead of scattered warnings | M | S3 | needs #290 closed first |
| **S5** | Tell the truth about where the machine is and whether it answers | L | S2 | #264, #277, #275, #232's install leg, part of #230 |
| **S6** | Templates that describe real machines | M | — | unblocks any guided flow |
| **S7** | The guided path, as a path through the same surface | M | S1,S2,S4,S5,S6 | subsumes #229, #238, #239 |
| **S8** | Resync the vendored runtime-config schema off v0.1.30 | S | upstream PR | prerequisite for any safe-state work |
| **S9** | Live-only safe-state editor | L | S5, S8 | anolis#251, #283, #237 |
| **S10** | `x-anolis-consequence` in anolis-provider-sdk | S | SDK + 3 provider releases | serves #289 |

**S1** — preflight reports `N passed · M skipped · K failed` with skips expanded
(kills `ok = all(c.get("ok") is not False)` at `launcher.py:394` rendering
`✓ All checks passed` at `Commission.svelte:490-494` — **verified**); bundle
architecture becomes a parameter; the Cancel button either cancels or does not exist;
SSE gains heartbeat + `Last-Event-ID`; `/fleet` stops 404-ing on deep-link
(**verified** missing from `_WORKSPACE_ROUTE_RE`).

**S3** — Compose's default view drops roughly 20 controls **without hiding a single
decision**. One predicate at `SchemaForm.svelte:58` plus `SchemaField`'s
nested-object loop, plus RuntimeForm's tier table with the structural guard test.
No upstream dependency. Also files the four existing #289 leaks
(`SchemaField.svelte:220` `key === "bus_path"`, `SchemaArray.svelte:48-50`,
`ProviderList.svelte:107-110`, `:156`) as follow-ups.

**S5** — liveness becomes three states (REACHABLE / NOT REACHABLE / UNKNOWN) backed by
an actual runtime probe, using the **existing** `~/.anolis/fleet.yaml` registry
(`fleet.py:240-282`, auto-registered after every remote deploy) rather than growing a
second one in `workbench.json`. The software stop becomes visible whenever a runtime
is reachable, **labelled with the machine it will stop** — because `operate.py:36-46`
resolves the proxy from `get_status()['active_project']`, not the selected project, so
an unlabelled always-visible stop targets the wrong rig.

**S9** closes the largest real capability gap in the product: **Operate tells the
operator their machine has no software safe state, and there is nowhere on earth in
this tool to declare one.** It cannot be done offline (see Rejected), and it cannot
even be written today — the machine-profile `safety` block is
`additionalProperties: false` with exactly one permitted property, and the vendored
runtime-config schema has **no `safety` key at all** (verified: vendored top-level
properties are `automation, http, logging, polling, providers, runtime, telemetry`;
upstream additionally has `health` and `safety`).

---

## Open questions — answered 2026-09-02

*Kept with their original reasoning; the decision taken is noted on each.*

**1. Will you spend two cross-repo schema changes?** One in `anolis` (add
title/description/default to `runtime-config.schema.json`, then resync the workbench
lock off v0.1.30), one in `anolis-provider-sdk` (`x-anolis-consequence`). Neither can
be done from the workbench — `verify-upstream-schema.py` enforces byte-identity with
the release asset.
→ **Lean: the runtime-config resync now, the SDK annotation filed today but off the
critical path.** The resync is one `just sync` plus a lock bump and it unblocks two
independent things. The SDK annotation needs an SDK release plus bread/ezo/sim
releases before a single field is annotated.

> **Decided:** the recommended split — runtime-config resync on the critical path (#345 / anolishq/anolis#293), SDK annotation filed now but not gating (anolis-provider-sdk#30).

**2. Does the workbench ever perform the install, or does it stay a bundle handoff?**
Issue #240's 2026-07-27 update says do *not* crown `install.sh --project`, and the
air-gapped staged bundle is a stated baseline. But every journey that ends "scp the
tarball, ssh in, run sudo bash install.sh" ends outside the product. **A guided flow
forces this decision by UI fiat if you do not make it first** — whichever path the
wizard's Install button uses becomes the blessed one.
→ **Lean: SSH install becomes first-class (prices #230 as P1), bundle stays for
air-gap at equal weight.** But "workbench never installs" is defensible and cheaper —
it just means the guided flow's last step is a command to copy, which is not what
"guided" promises.

> **Decided:** moot for this epic — the install leg stays with #232, whose 2026-07-27 reframe already locks the answer (`--project` is an online-convenience path only; UIs are additive surfaces that must never be required).

**3. Who is the target user — a fresh-Pi novice with a normal sudo password, or a
bench developer with passwordless sudo and known_hosts populated?** Today
`executor.py:108-119` runs sudo with no pty/`-S`/askpass, and `executor.py:212` uses
`paramiko.RejectPolicy()`, so a fresh Pi is rejected on first contact, invisibly.
→ **Lean: bench developer first, fresh-Pi novice as the stated destination.** Shipping
a novice-facing flow on top of credential plumbing that does not work is how you get
the current onboarding screen a second time. Say in the copy that shell access is
assumed.

> **Decided:** moot for this epic. #232's locked scope already assumes passwordless sudo on a bench machine, with #230 as the upgrade path.

**4. Is safe-state authoring in scope at all?** All three designs invented it; all six
critics killed it. But it is the largest genuine capability gap in the product.
→ **Lean: a read-only reporting surface now (say what the runtime says, name the file
to edit), the live editor later as its own slice.** It should not be smuggled into a
simplification epic.

> **Decided:** reporting surface now (#346); the live editor filed separately against anolishq/anolis#251 and #283.

**5. Do you accept a global depth control at all, or only per-section disclosure?**
I am recommending three positions but **redefining what they do** — default expansion
state, never a filter. That is meaningfully less than what you asked for.
→ **Lean: the redefined control.** A true global filter is unshippable here (it can
hide a field named by a save error). Per-section-only is fine and cheaper; the only
thing the global control buys is a legible statement that the app has more depth when
you need it.

> **Decided:** the redefined three-position control — a floor, not a filter (#340).

**6. Do the three tabs stay?** The task-oriented design makes the strongest case that
Compose/Commission/Operate is the server's module layout wearing a tab bar. But it
touches three route owners, breaks the e2e smoke that asserts the three URLs by name,
and conflicts textually with PR #319.
→ **Lean: keep the tabs, revisit after the readiness panel has demonstrated what they
hide.** If after S4 ships the panel is mostly rows saying "this is in a tab you did
not know was dead", the rewrite has earned itself.

> **Decided:** keep the tabs. Revisit after #342 ships. Recorded as a risk line in #337, deliberately not filed.

---

## Rejected, with reasons — so you can overrule

- **A global Simple/Advanced/Expert filter as the primary control.** Difficulty does
  not track the axis, and a true filter can hide a field named by a save error.
- **A workbench-side table mapping provider field paths to tiers.** Direct #289
  violation; unversioned against sha256-locked envelopes; rots on the first rename.
- **Deriving tier purely from `required`/`default`/`const`.** Measurably wrong here:
  the runtime schema declares zero defaults and requires only `providers`, so it tiers
  all 18 RuntimeForm fields identically — useless exactly where the form is longest.
- **Install-first, then author against observed hardware.** The measurements it buys
  are unsound (scan write-probes a shared bus and silences the collision gate) or
  unreachable (four consecutive steps go dark on a remote Pi).
- **Offline authoring of `safety.safe_state`.** `safe_state.setpoints[]` are executed
  verbatim against real actuators during a stop (`safe_state.cpp:143-169`); the
  coverage rule fails closed against the *live* registry, so a mistyped function name
  silently yields `SafeStateKind::None` while a document-derived UI shows "declared".
  `safe_state.hpp:44-51` names this exact construct as how anolis#251 happened.
- **`zero_is_safe` as a peer rung in any guided surface.** Cheapest click, widest blast
  radius. `run_zero_call` commands every actuating function with 0 — an *action*, not
  an inhibit — and unlike setpoints it has **no coverage check**. A boolean ticked once
  against a two-device inventory silently extends "zero is safe" to every device added
  afterwards, forever, with no re-prompt.
- **Auto-running preflight on entering Commission.** Neither read-only nor cheap:
  `materialize_launch_config` writes to disk and `_check_config_binary` spawns every
  provider binary with a 10s timeout. Against the only shipped template it would
  permanently grey out Launch with no control able to remedy it. The real bug is that
  preflight is opt-in; invalidate results on save instead, and fix the template.
- **Coupling Compose's variant selector to `doc.launch.variant`.** The bug is real
  (nothing in `frontend/src` writes `doc.launch.variant`, so editing the automation
  variant and pressing Launch silently runs manual) — but merging the concepts means
  *selecting a variant to look at it* re-points dev-launch at autonomous actuation, on
  a host that in appliance mode is the Pi wired to the actuators.
- **Exposing `http.auth_enabled` as a checkbox.** `config.cpp:240-241` hard-refuses
  startup without `auth_token`, and the token cannot live in the artifact. It is a
  one-click path to a runtime that will not boot, discovered on the rig.
- **A scored task checklist with DONE states.** Deliberate omissions are
  indistinguishable from incomplete ones.
- **Deleting the template selector, `host_paths`, the Influx token, or collapsing
  Commission into an "advanced editor".** Disclose, don't delete — Commission is 681
  lines of the daily bench loop, not an advanced surface.

---

## Corrections and caveats

- **One agent claim was wrong and I corrected it.** An agent reported that
  `automation.mode_transition_hooks` "appears nowhere in `anolis_workbench/` or
  `frontend/src`". It does — at `canonical.ts:140` and `canonical.py:300`, as an
  **inertness check**: the workbench detects the key's presence to refuse a
  non-inert `manual` variant. The real finding is sharper and still holds: the
  workbench *validates against* hooks but offers **no way to author them**, while
  `Compose.svelte:92-98` writes an automation variant containing only
  `{enabled, behavior_tree}`. That is a capability gap, not a tiering problem, and no
  disclosure triangle fixes it.
- **I verified nine load-bearing claims directly** (cancel-flag readers, preflight `ok`
  computation, `_WORKSPACE_ROUTE_RE`, template `host_paths`, the vendored-vs-upstream
  `safety` key, bread's `command_watchdog_ms` and `hardware.required`,
  `Commission.svelte`'s hardcoded arch, and the unguarded Rollback button). All nine
  held.
- **Not independently verified by me:** the C++ line references into `anolis` and
  `anolis-provider-sdk` (`safe_state.cpp`, `crumbs_transport.cpp`, `config.hpp/cpp`,
  `mode_manager.hpp`) and the `install.sh` line numbers. Agents cited them from the
  checked-out sources; spot-check before quoting any of them in an issue body.
- **The Rollback button** (`Home.svelte:349`) has no `ConfirmModal`, no statement of
  which version it rolls back to, and no check of `runtimeStatus.running` — it swaps
  binaries and restarts the service on a target that may be driving hardware right
  now. It is not in any slice above because it is a standalone bug; file it separately.
- **The backlog reconciliation found one clean result:** a keyword sweep of all issues
  in both repos (open and closed) for wizard / guided / novice / beginner /
  progressive-disclosure / complexity-tier returns nothing but incidental hits. **The
  authoring-surface half of your ask is entirely untracked.** The install-path half is
  already dense with planned work — five open issues collide in ~35 lines of
  `Onboarding.svelte`. That is where a new epic adds real, non-duplicative value, and
  where it most risks stepping on #232.
- **One meta-point worth taking seriously:** a wizard and a tier system are not the same
  mechanism and should not ship as one deliverable. Tiering is a property of every
  form, permanently, and is the cheaper, lower-risk, immediately-buildable half.
  Sequencing them as one epic makes the cheap half wait behind the gated half.
