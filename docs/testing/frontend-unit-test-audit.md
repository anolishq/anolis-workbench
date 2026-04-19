# Frontend Unit Test Audit

Date: 2026-04-19

## Scope

Audit of frontend unit-test coverage for:

1. `frontend/src/lib/api.ts`
2. `frontend/src/lib/guards.ts`
3. `frontend/src/lib/operate-contracts.ts`
4. `frontend/src/lib/operate-events.ts`

## Current Baseline

1. Runner: Vitest (`vitest run --config vitest.config.ts`).
2. Tests focus on deterministic unit behavior with explicit boundary mocking.
3. Coverage reporting is enabled and threshold-gated in CI.
4. Browser smoke tests (Playwright) remain complementary and do not replace deterministic unit checks for parser/normalizer edge cases.

## Gaps Identified

1. `api.ts`:
   - `downloadBlob` behavior required explicit validation.
   - `fetchJson` error branches needed coverage for:
     - non-JSON success payload
     - non-JSON error payload
     - empty error payload fallback
2. `guards.ts`:
   - negative/no-prompt paths needed explicit assertions.
   - project-switch warning suppression cases needed explicit assertions.
3. `operate-contracts.ts`:
   - many exported normalizers/parsers required coverage across invalid/partial payloads.
   - branch-heavy validation (`coerceParameterValue`) required bounds and unsupported-type checks.
   - `renderBtOutline` required direct unit coverage.
4. `operate-events.ts`:
   - reconnect/parse-error/init-failure branches required explicit tests.
   - status transitions and timer-driven stale detection required explicit assertions.

## Test Strategy

1. Keep tests deterministic:
   - mock only external boundaries (`fetch`, `EventSource`, timers, DOM shims).
   - avoid asserting implementation trivia when behavior-level assertions are possible.
2. Cover success + failure branches for each exported function in `frontend/src/lib/*`.
3. Maintain a coverage gate focused on `frontend/src/lib/**/*` so migration decisions are based on measured confidence.

## Coverage Gate

Enforced thresholds for `frontend/src/lib/**/*`:

1. statements: 85%
2. branches: 75%
3. functions: 85%
4. lines: 85%

These thresholds are intentionally non-trivial and can be raised once the TypeScript migration and release path are stable.

## Out of Scope for This Pass

1. Full component-level unit tests for Svelte routes/components.
2. Contract-level backend API integration (already covered by Python contract tests).
3. End-to-end browser workflow coverage expansion (already partly covered by Playwright smoke lane).

## Implementation Status

Completed in this pass:

1. Vitest runner configured (`frontend/vitest.config.ts`) with V8 coverage + thresholds.
2. Frontend test scripts added in `frontend/package.json`:
   - `test:unit`
   - `test:unit:watch`
   - `test:unit:coverage`
3. Unit tests migrated to TypeScript under `frontend/tests/unit/`:
   - `api.test.ts`
   - `guards.test.ts`
   - `operate-contracts.test.ts`
   - `operate-events.test.ts`
4. Legacy Node-runner `.mjs` frontend unit tests removed from `tests/unit/`.
5. CI test lane switched to Vitest coverage execution.
6. Frontend library modules migrated from JS to TS and imports updated across app + tests.

Verification snapshot:

1. Vitest: `38 passed`.
2. Coverage (`frontend/src/lib/**/*` aggregate):
   - statements: `98.65%`
   - branches: `82.78%`
   - functions: `95.55%`
   - lines: `98.65%`
3. `npm run check`: `0 errors`, `29 warnings` (existing Svelte a11y label association warnings).
