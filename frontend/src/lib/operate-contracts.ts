// operate-contracts.ts — data extraction / normalization for the Operate workspace

import type {
  AutomationCallFailure,
  AutomationStatus,
  AutomationVersion,
  Device,
  DeviceCapabilities,
  DeviceStateValue,
  EstopStatus,
  ExecutionStatus,
  FunctionArgSpec,
  FunctionSpec,
  OperatorBlock,
  ParameterDefinition,
  StartRequirement,
  ProviderHealth,
  RuntimeApiStatus,
  SoftwareSafeState,
  StartupSafeStateStatus,
  StopChainExpect,
  StopChainState,
  StopChainStatus,
  UnknownRecord,
} from "./contracts";

/**
 * Runtime modes accepted by `POST /v0/mode` and reported by `GET /v0/mode`.
 * MUST stay in sync with the `RuntimeMode` enum in the vendored runtime-http
 * OpenAPI contract — a unit test guards against drift.
 */
export const RUNTIME_MODES = ["MANUAL", "AUTO", "IDLE", "FAULT"] as const;

export type RuntimeMode = (typeof RUNTIME_MODES)[number];

/**
 * The modes `POST /v0/mode` accepts from `current`, `current` first: the
 * runtime's ModeManager::is_valid_transition (anolis
 * core/automation/mode_manager.cpp). Any mode may go to FAULT; FAULT goes only
 * to MANUAL; AUTO and IDLE go only to MANUAL. An unknown mode offers all (wb#692).
 */
export function modeTargets(current: string): RuntimeMode[] {
  const allowed: Record<RuntimeMode, RuntimeMode[]> = {
    MANUAL: ["MANUAL", "AUTO", "IDLE", "FAULT"],
    AUTO: ["AUTO", "MANUAL", "FAULT"],
    IDLE: ["IDLE", "MANUAL", "FAULT"],
    FAULT: ["MANUAL", "FAULT"],
  };
  return (RUNTIME_MODES as readonly string[]).includes(current)
    ? allowed[current as RuntimeMode]
    : [...RUNTIME_MODES];
}

/**
 * The text for a failed call. A call the e-stop latch refused names the runtime
 * endpoint that releases it; Operate has a control for that (wb#692).
 */
export function callErrorText(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  if (message.startsWith("Actuation latched by e-stop")) {
    return "Refused: the software stop latch holds actuation. Clear it with Clear latch… above.";
  }
  return `Error: ${message}`;
}

const EXECUTION_STATUSES: ReadonlySet<ExecutionStatus> = new Set<ExecutionStatus>([
  "idle",
  "running",
  "blocked",
  "failed",
  "completed",
  "unknown",
]);

export function normalizeExecutionStatus(value: unknown): ExecutionStatus {
  const v = String(value ?? "")
    .trim()
    .toLowerCase() as ExecutionStatus;
  return EXECUTION_STATUSES.has(v) ? v : "unknown";
}

type ParameterType = "double" | "int64" | "bool" | "string";

export type CoerceParameterValueInput = {
  type: unknown;
  rawValue: unknown;
  min?: number | string;
  max?: number | string;
  allowedValues?: Array<string | number>;
};

function asObject(v: unknown): UnknownRecord {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as UnknownRecord) : {};
}

function asArray<T = unknown>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

function toFinite(v: unknown): number | null {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

const PARAMETER_TYPES = new Set<ParameterType>(["double", "int64", "bool", "string"]);
const INT64_MIN = -9223372036854775808n;
const INT64_MAX = 9223372036854775807n;
const JS_SAFE_MIN = BigInt(Number.MIN_SAFE_INTEGER);
const JS_SAFE_MAX = BigInt(Number.MAX_SAFE_INTEGER);

export function extractDevices(payload: unknown): Device[] {
  return asArray(asObject(payload).devices) as Device[];
}

export function extractProvidersHealth(payload: unknown): ProviderHealth[] {
  return asArray(asObject(payload).providers) as ProviderHealth[];
}

/**
 * Devices whose provider reports the SDK-reserved `watchdog_tripped` health
 * metric as "true", as "provider/device" (wb#572). Only the exact value
 * counts; an absent key makes no claim.
 */
export function watchdogTrippedDevices(providers: ProviderHealth[]): string[] {
  const out: string[] = [];
  for (const provider of providers) {
    for (const device of provider.devices ?? []) {
      if (device.reported?.metrics?.watchdog_tripped === "true") {
        out.push(`${provider.provider_id}/${device.device_id}`);
      }
    }
  }
  return out;
}

export function extractCapabilities(payload: unknown): DeviceCapabilities {
  const root = asObject(payload);
  const caps = asObject(root.capabilities);
  return {
    ...caps,
    signals: asArray(caps.signals) as UnknownRecord[],
    functions: normalizeFunctionSpecs(caps.functions),
  } as DeviceCapabilities;
}

export function normalizeFunctionSpecs(functions: unknown): FunctionSpec[] {
  if (!Array.isArray(functions)) return [];
  return (functions as UnknownRecord[])
    .map((func, i) => {
      const functionId = toFinite(func.function_id) ?? i + 1;
      const name =
        (typeof func.name === "string" && func.name.trim()) ||
        (typeof func.function_name === "string" && func.function_name.trim()) ||
        `Function ${functionId}`;
      const description =
        (typeof func.label === "string" && func.label.trim()) ||
        (typeof func.description === "string" && func.description.trim()) ||
        "";
      return {
        ...func,
        function_id: functionId,
        name,
        function_name: name,
        display_name: name,
        label: description,
        description,
        args: normalizeFunctionArgs(func.args),
      } as FunctionSpec;
    })
    .sort((a, b) =>
      a.function_id !== b.function_id
        ? a.function_id - b.function_id
        : String(a.display_name).localeCompare(String(b.display_name)),
    );
}

export function normalizeFunctionArgs(args: unknown): FunctionArgSpec[] {
  if (Array.isArray(args)) {
    return (args as UnknownRecord[])
      .map((a, i) => normalizeArgSpec(asObject(a), `arg_${i + 1}`))
      .filter((a) => a.name !== "");
  }
  if (args && typeof args === "object") {
    return Object.entries(args as UnknownRecord)
      .map(([name, spec]) => normalizeArgSpec(asObject(spec), name))
      .filter((a) => a.name !== "")
      .sort((a, b) => a.name.localeCompare(b.name));
  }
  return [];
}

export function normalizeArgSpec(arg: UnknownRecord, fallback = ""): FunctionArgSpec {
  const name = (typeof arg.name === "string" && arg.name.trim()) || fallback;
  if (!name.trim()) return { name: "", type: "string", required: true };
  return {
    name: name.trim(),
    type: (typeof arg.type === "string" && arg.type.trim()) || "string",
    required: arg.required !== false,
    min: arg.min as number | string | undefined,
    max: arg.max as number | string | undefined,
    allowed_values: arg.allowed_values as Array<string | number> | undefined,
  };
}

/**
 * The values a string arg is limited to, or [] when it is unconstrained. The
 * runtime emits `allowed_values` only on string args and refuses a call
 * outside them (anolis#467).
 */
export function argAllowedValues(arg: FunctionArgSpec): string[] {
  if (arg.type !== "string" || !Array.isArray(arg.allowed_values)) return [];
  return arg.allowed_values.map(String);
}

/** Throws when `value` is outside the arg's allowed values (wb#576). */
export function checkAllowedValue(arg: FunctionArgSpec, value: string): void {
  const allowed = argAllowedValues(arg);
  if (allowed.length > 0 && !allowed.includes(value)) {
    throw new Error(`${arg.name} must be one of ${allowed.join(", ")}, not '${value}'`);
  }
}

export function extractDeviceStateValues(payload: unknown): DeviceStateValue[] {
  return asArray(asObject(payload).values).map((s) => {
    const src = asObject(s);
    return {
      ...src,
      timestamp_ms: toFinite(src.timestamp_ms) ?? toFinite(src.timestamp_epoch_ms) ?? 0,
    } as DeviceStateValue;
  });
}

export function extractMode(payload: unknown): string | null {
  const r = asObject(payload);
  return typeof r.mode === "string" ? r.mode : null;
}

export function extractRuntimeStatus(payload: unknown): RuntimeApiStatus {
  const r = asObject(payload);
  return {
    status: asObject(r.status) as RuntimeApiStatus["status"],
    version: typeof r.version === "string" ? r.version : "",
    mode: typeof r.mode === "string" ? r.mode : "UNKNOWN",
    uptime_seconds: toFinite(r.uptime_seconds) ?? 0,
    polling_interval_ms: toFinite(r.polling_interval_ms) ?? 0,
    device_count: toFinite(r.device_count) ?? 0,
    providers: asArray(r.providers) as RuntimeApiStatus["providers"],
    device_loss_latched: asArray(r.device_loss_latched).map(String),
  };
}

export function extractAutomationVersion(payload: unknown): AutomationVersion | null {
  if (payload === null || typeof payload !== "object") return null;
  const r = payload as UnknownRecord;
  return {
    engine_kind: typeof r.engine_kind === "string" ? r.engine_kind : "",
    id: typeof r.id === "string" ? r.id : "",
    digest: typeof r.digest === "string" ? r.digest : "",
    digest_scope: typeof r.digest_scope === "string" ? r.digest_scope : "",
  };
}

export function extractAutomationStatus(payload: unknown): AutomationStatus {
  const r = asObject(payload);
  return {
    execution_status: normalizeExecutionStatus(r.execution_status),
    execution_reason:
      typeof r.execution_reason === "string" && r.execution_reason.trim()
        ? r.execution_reason
        : null,
    automation_version: extractAutomationVersion(r.automation_version),
    last_evaluation_at_epoch_ms: toFinite(r.last_evaluation_at_epoch_ms) ?? null,
    run_id: typeof r.run_id === "string" && r.run_id.trim() ? r.run_id : null,
    last_error: typeof r.last_error === "string" && r.last_error.trim() ? r.last_error : null,
    call_failures: asArray(r.call_failures).map((f) => {
      const c = asObject(f);
      return {
        device_handle: String(c.device_handle ?? ""),
        function: String(c.function ?? ""),
        consecutive_failures: toFinite(c.consecutive_failures) ?? 0,
        last_error: String(c.last_error ?? ""),
        last_failure_at_epoch_ms: toFinite(c.last_failure_at_epoch_ms) ?? 0,
      };
    }),
  };
}

/**
 * `call_failures` with an `automation_call_failed` event applied, so a
 * failure shows between status polls. The event replaces the entry for its
 * device and function, or adds one. A success sends no event; the next poll
 * shows it.
 */
export function applyCallFailedEvent(
  failures: AutomationCallFailure[],
  payload: UnknownRecord,
): AutomationCallFailure[] {
  const entry: AutomationCallFailure = {
    device_handle: String(payload.device_handle ?? ""),
    function: String(payload.function ?? ""),
    consecutive_failures: toFinite(payload.consecutive_failures) ?? 0,
    last_error: String(payload.error ?? ""),
    last_failure_at_epoch_ms: toFinite(payload.timestamp_ms) ?? 0,
  };
  const same = (f: AutomationCallFailure) =>
    f.device_handle === entry.device_handle && f.function === entry.function;
  return failures.some(same) ? failures.map((f) => (same(f) ? entry : f)) : [...failures, entry];
}

/**
 * A line per automation call failing now (anolis#678): which call, its
 * failures in a row, and its last error. A call that has succeeded since its
 * last failure is not failing.
 */
export function callFailureLines(failures: AutomationCallFailure[]): string[] {
  return failures
    .filter((f) => f.consecutive_failures > 0)
    .map((f) => {
      const times =
        f.consecutive_failures === 1 ? "once" : `${f.consecutive_failures} times in a row`;
      return `${f.device_handle} ${f.function}: failed ${times}; last error: ${f.last_error}`;
    });
}

export function extractAutomationTree(payload: unknown): string {
  const r = asObject(payload);
  return typeof r.tree === "string" ? r.tree : "";
}

export function normalizeParameterType(type: unknown): ParameterType | null {
  const t = String(type ?? "").trim() as ParameterType;
  return PARAMETER_TYPES.has(t) ? t : null;
}

export function extractParameters(payload: unknown): ParameterDefinition[] {
  return asArray(asObject(payload).parameters)
    .filter((p) => p && typeof (p as UnknownRecord).name === "string")
    .map((p) => {
      const record = asObject(p);
      return {
        ...record,
        name: String(record.name).trim(),
        type: normalizeParameterType(record.type) ?? String(record.type ?? ""),
      } as ParameterDefinition;
    })
    .filter((p) => p.name !== "")
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function normalizeProviderHealthQuality(
  quality: unknown,
): "OK" | "FAULT" | "UNAVAILABLE" | "UNKNOWN" {
  const v = String(quality || "UNKNOWN").toUpperCase();
  if (v === "OK" || v === "READY" || v === "AVAILABLE") return "OK";
  if (v === "FAULT") return "FAULT";
  if (v === "UNAVAILABLE" || v === "STALE") return "UNAVAILABLE";
  return "UNKNOWN";
}

/**
 * Software safe-state ladder rungs, mirroring the `SoftwareSafeState` enum in
 * the vendored runtime-http OpenAPI contract. A unit test guards against drift.
 */
export const SOFTWARE_SAFE_STATES = ["none", "hooks", "setpoints", "zero"] as const;

/**
 * The first runtime whose `uncovered_actuating_functions` counts the
 * actuating functions the active rung will not drive (anolishq/anolis#253,
 * anolishq/anolis PR #369). Before it, the field counts functions with no
 * matching setpoint, which overstates under hooks and is false under zero.
 */
export const DISPATCHABLE_COVERAGE_SINCE = "0.1.45";

const SEMVER = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/;

/**
 * Whether `version` is at least `min` (a release `X.Y.Z`) by semver
 * precedence. A pre-release of `min` is older than `min`. An unknown or
 * unparsable version is never at least anything.
 */
export function versionAtLeast(version: string | null | undefined, min: string): boolean {
  const v = SEMVER.exec((version ?? "").trim());
  const m = SEMVER.exec(min);
  if (!v || !m) return false;
  for (let i = 1; i <= 3; i += 1) {
    const diff = Number(v[i]) - Number(m[i]);
    if (diff !== 0) return diff > 0;
  }
  return v[4] === undefined;
}

/**
 * Normalize a software-safe-state value. An unrecognized value degrades to the
 * *weakest* claim ("none") — the UI must never advertise a stop rung the runtime
 * did not declare.
 */
export function normalizeSoftwareSafeState(value: unknown): SoftwareSafeState {
  const v = String(value ?? "")
    .trim()
    .toLowerCase() as SoftwareSafeState;
  return (SOFTWARE_SAFE_STATES as readonly string[]).includes(v) ? v : "none";
}

/**
 * Extract the `estop` block from a `GET /v0/runtime/status` body. Returns null
 * when the block is absent or not an object (the contract keeps it optional) —
 * the UI must not render a stop control it cannot describe.
 */
export function extractEstopStatus(payload: unknown): EstopStatus | null {
  const root = asObject(payload);
  if (!root.estop || typeof root.estop !== "object" || Array.isArray(root.estop)) {
    return null;
  }
  const r = asObject(root.estop);
  return {
    latched: r.latched === true,
    software_safe_state: normalizeSoftwareSafeState(r.software_safe_state),
    // toFinite(null) is 0, but an absent latch time must stay null.
    latched_at_epoch_ms: r.latched_at_epoch_ms == null ? null : toFinite(r.latched_at_epoch_ms),
    uncovered_actuating_functions: toFinite(r.uncovered_actuating_functions) ?? 0,
  };
}

/**
 * Extract the `stop_chain` block from a `GET /v0/runtime/status` body (wb#631).
 * Null when it is absent: the project configures no stop chain, or the runtime
 * is older than anolis 0.1.53. A state this build does not know reads as
 * unknown, which the runtime also holds the latch on.
 */
export function extractStopChain(payload: unknown): StopChainStatus | null {
  const raw = asObject(payload).stop_chain;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = asObject(raw);
  const state: StopChainState = r.state === "in_force" || r.state === "clear" ? r.state : "unknown";
  return {
    state,
    reason: typeof r.reason === "string" ? r.reason : "",
    signal: typeof r.signal === "string" ? r.signal : "",
    devices: asArray(r.devices).map(String),
    expectations: extractStopChainExpectations(r.expectations),
    expected_losses: asArray(r.expected_losses).map(String),
    mismatches: asArray(r.mismatches).flatMap((entry) => {
      const m = asObject(entry);
      if (typeof m.device !== "string") return [];
      return [
        {
          device: m.device,
          expect: typeof m.expect === "string" ? m.expect : "",
          reason: typeof m.reason === "string" ? m.reason : "",
          since_epoch_ms: m.since_epoch_ms == null ? null : toFinite(m.since_epoch_ms),
        },
      ];
    }),
    since_epoch_ms: r.since_epoch_ms == null ? null : toFinite(r.since_epoch_ms),
  };
}

/** The declared expectations by handle; absent before anolis 0.1.55. */
function extractStopChainExpectations(raw: unknown): Record<string, StopChainExpect> {
  const out: Record<string, StopChainExpect> = {};
  for (const [handle, expect] of Object.entries(asObject(raw))) {
    if (expect === "power_loss" || expect === "reports_estop") out[handle] = expect;
  }
  return out;
}

/**
 * Extract the `startup_safe_state` block from a `GET /v0/runtime/status` body.
 * Returns null when it is absent (a runtime older than anolis 0.1.45) or not an
 * object, so the UI shows nothing rather than guess.
 */
export function extractStartupSafeState(payload: unknown): StartupSafeStateStatus | null {
  const root = asObject(payload);
  const raw = root.startup_safe_state;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = asObject(raw);
  return {
    on_startup: r.on_startup === "leave" ? "leave" : "safe_state",
    ran: r.ran === true,
    software_safe_state: normalizeSoftwareSafeState(r.software_safe_state),
    safe_state_achieved: r.safe_state_achieved === true,
    failed_actions: asArray(r.failed_actions)
      .map(asObject)
      .map((a) => ({
        device_handle: String(a.device_handle ?? "?"),
        function: String(a.function ?? "?"),
        success: a.success === true,
        ...(typeof a.error === "string" ? { error: a.error } : {}),
      })),
    skipped_actions: asArray(r.skipped_actions)
      .map(asObject)
      .map((a) => ({
        device_handle: String(a.device_handle ?? "?"),
        function: String(a.function ?? "?"),
        reason: String(a.reason ?? "absent"),
      })),
  };
}

/**
 * Format the runtime's startup safe-state report into operator-facing text.
 * `safe_state_achieved` means the rung issued at least one call and every
 * call was accepted by its provider, not that the outputs reached a safe
 * state, so the text says no more than that. `ok` is false when a rung ran
 * and the runtime's verdict is false, or when any call was skipped.
 */
export function summarizeStartupSafeState(s: StartupSafeStateStatus): {
  text: string;
  ok: boolean;
  /** One line per failure reason, naming the calls that hit it, then one per skipped call (wb#692). */
  calls: string[];
} {
  const head = `Startup safe state (${s.software_safe_state})`;
  const byError = new Map<string, string[]>();
  for (const a of s.failed_actions) {
    const error = a.error ?? "failed";
    byError.set(error, [...(byError.get(error) ?? []), `${a.device_handle}/${a.function}`]);
  }
  const calls = [
    ...[...byError].map(([error, names]) => `${names.join(", ")}: ${error}`),
    ...s.skipped_actions.map((a) => `${a.device_handle}/${a.function} skipped: ${a.reason}`),
  ];
  // A skipped call's device was absent at startup, and is not driven when it
  // appears later (anolis#461): never ok, whatever the calls that ran did.
  const skipped =
    s.skipped_actions.length === 0
      ? ""
      : `${s.skipped_actions.length} call(s) skipped, device absent at startup.`;
  if (s.ran && s.safe_state_achieved) {
    const text = `${head}: every call it issued was accepted by its provider.`;
    return skipped ? { text: `${text} ${skipped}`, ok: false, calls } : { text, ok: true, calls };
  }
  if (s.ran && s.failed_actions.length === 0) {
    if (skipped) {
      return { text: `${head} issued no call: ${skipped}`, ok: false, calls };
    }
    // Otherwise only the zero rung with no actuating function issues no call.
    return { text: `${head} ran but issued no call, so nothing was driven.`, ok: false, calls };
  }
  if (s.ran) {
    const text = `${head}: ${s.failed_actions.length} call(s) failed.`;
    return { text: skipped ? `${text} ${skipped}` : text, ok: false, calls };
  }
  if (s.on_startup === "leave") {
    return {
      text: "Outputs were left as they were at startup (on_startup: leave).",
      ok: true,
      calls,
    };
  }
  return {
    text: "No software safe state is declared, so nothing ran at startup.",
    ok: true,
    calls,
  };
}

/**
 * The Runtime Status badge, from machine state (wb#557). `status.code` is the
 * response envelope: it says the status request succeeded, so it decides only
 * between loaded and UNAVAILABLE, never whether the machine is OK.
 */
export function summarizeRuntimeHealth(
  status: RuntimeApiStatus | null,
  estop: EstopStatus | null,
  startup: StartupSafeStateStatus | null,
  stopChain: StopChainStatus | null = null,
): {
  label: "OK" | "ATTENTION" | "UNAVAILABLE";
  cls: "ok" | "fault" | "unavailable";
  reasons: string[];
} {
  if (!status || String(status.status?.code || "").toUpperCase() !== "OK") {
    return { label: "UNAVAILABLE", cls: "unavailable", reasons: [] };
  }
  const reasons: string[] = [];
  if (stopChain?.state === "in_force") reasons.push("emergency stop in force");
  if (stopChain?.state === "unknown") reasons.push("emergency stop status unknown");
  for (const m of stopChain?.mismatches ?? []) reasons.push(`stop not reaching ${m.device}`);
  if (estop?.latched) reasons.push("software stop latched");
  if (startup && !summarizeStartupSafeState(startup).ok) {
    reasons.push("startup safe state not achieved");
  }
  for (const p of status.providers) {
    if (p.state !== "AVAILABLE") reasons.push(`provider ${p.provider_id} ${p.state}`);
  }
  // A device the stop removed power from is the stop, not a device fault (wb#631).
  const stopped = new Set(stopChain?.expected_losses ?? []);
  for (const handle of status.device_loss_latched) {
    reasons.push(
      stopped.has(handle)
        ? `device ${handle} stopped by the emergency stop`
        : `device ${handle} lost (latched)`,
    );
  }
  return reasons.length > 0
    ? { label: "ATTENTION", cls: "fault", reasons }
    : { label: "OK", cls: "ok", reasons };
}

/**
 * Format a `POST /v0/estop` response (EstopResponse) into operator-facing text.
 * `ok` is false whenever the operator should not trust that the machine is safe
 * (an action failed, the latch did not engage, or a FAULT transition failed).
 */
export function summarizeEstopResponse(payload: unknown): { text: string; ok: boolean } {
  const r = asObject(payload);
  const latched = r.latched === true;
  const rung = normalizeSoftwareSafeState(r.software_safe_state);
  const actions = asArray(r.actions).map(asObject);
  const failures = actions.filter((a) => a.success === false);
  const parts: string[] = [];
  let ok = true;

  // Never assert "engaged"/"latched" when the latch did not actually engage —
  // this defensive branch exists precisely to be honest about a failed stop.
  if (latched) {
    parts.push(`Software stop engaged (${rung}).`);
  } else {
    parts.push("WARNING: the latch did not engage — the machine may not be safe.");
    ok = false;
  }

  if (actions.length === 0 && rung === "none") {
    if (latched) {
      const warning = typeof r.warning === "string" && r.warning.trim() ? r.warning.trim() : "";
      parts.push(
        warning ||
          "Actuation is latched, but no software safe-state is declared — outputs were NOT driven.",
      );
    }
    // When the latch failed, the leading warning already conveys nothing is safe.
  } else if (failures.length > 0) {
    ok = false;
    const detail = failures
      .map((a) => `${a.device_handle ?? "?"}/${a.function ?? "?"}: ${a.error ?? "failed"}`)
      .join("; ");
    parts.push(`${failures.length} of ${actions.length} action(s) FAILED: ${detail}`);
  } else if (actions.length > 0) {
    parts.push(`${actions.length} safe-state action(s) succeeded.`);
  }

  if (r.fault_engaged === false) {
    parts.push("FAULT transition did not engage.");
    ok = false;
  }

  if (!ok) {
    parts.push("Use the hardware e-stop to make the machine safe.");
  }

  return { text: parts.join(" "), ok };
}

export function deriveOperateAvailability(
  statusPayload: UnknownRecord | null | undefined,
  projectName: string,
): {
  available: boolean;
  reason: "stopped" | "different_project" | "available";
  message: string;
  runningProject: string;
} {
  const running = Boolean(statusPayload?.running);
  const runningProject =
    typeof statusPayload?.active_project === "string" ? statusPayload.active_project : "";
  if (!running) {
    return {
      available: false,
      reason: "stopped",
      message: "Runtime is stopped. Start runtime from Commission to operate this project.",
      runningProject,
    };
  }
  if (runningProject !== projectName) {
    return {
      available: false,
      reason: "different_project",
      message: `Runtime is running for project "${runningProject}". Stop it before operating "${projectName}".`,
      runningProject,
    };
  }
  return { available: true, reason: "available", message: "", runningProject };
}

export function coerceParameterValue(input: CoerceParameterValueInput): number | boolean | string {
  const { type, rawValue, min, max, allowedValues } = input;
  const ntype = normalizeParameterType(type);
  if (!ntype) throw new Error("Unsupported parameter type");
  const src = String(rawValue ?? "").trim();
  let value: number | boolean | string;

  if (ntype === "int64") {
    if (!/^-?\d+$/.test(src)) throw new Error("Invalid integer");
    let n: bigint;
    try {
      n = BigInt(src);
    } catch {
      throw new Error("Invalid integer");
    }
    if (n < INT64_MIN || n > INT64_MAX) throw new Error("Out-of-range int64");
    if (n < JS_SAFE_MIN || n > JS_SAFE_MAX) throw new Error("int64 exceeds browser-safe range");
    value = Number(n);
  } else if (ntype === "double") {
    value = Number(src);
    if (Number.isNaN(value)) throw new Error("Invalid number");
  } else if (ntype === "bool") {
    value = src.toLowerCase() === "true";
  } else {
    value = src;
  }

  if ((ntype === "int64" || ntype === "double") && typeof value === "number") {
    const mn = min !== undefined ? Number(min) : undefined;
    const mx = max !== undefined ? Number(max) : undefined;
    if (Number.isFinite(mn) && value < (mn as number)) {
      throw new Error(`Value below minimum (${mn})`);
    }
    if (Number.isFinite(mx) && value > (mx as number)) {
      throw new Error(`Value above maximum (${mx})`);
    }
  }

  if (ntype === "string" && Array.isArray(allowedValues) && allowedValues.length > 0) {
    const allowed = allowedValues.map(String);
    if (!allowed.includes(String(value))) {
      throw new Error(`Value must be one of: ${allowed.join(", ")}`);
    }
  }
  return value;
}

export function renderBtOutline(
  xmlDoc: Document,
  node: Element | null = null,
  indent = 0,
  isLast = true,
): string {
  if (!node) {
    const root = xmlDoc.querySelector("BehaviorTree");
    if (!root) return "No BehaviorTree found.";
    return renderBtOutline(xmlDoc, root, 0, true);
  }
  const prefix = indent === 0 ? "" : " ".repeat((indent - 1) * 2) + (isLast ? "\\- " : "|- ");
  const name = node.getAttribute("name") || "";
  let out = `${prefix}${node.tagName}${name ? ` "${name}"` : ""}\n`;
  const children = Array.from(node.children) as Element[];
  for (let i = 0; i < children.length; i += 1) {
    out += renderBtOutline(xmlDoc, children[i], indent + 1, i === children.length - 1);
  }
  return out;
}

/** Where a machine line's link takes the operator. */
export type MachineLineTarget = "mode" | "watchdog-trips" | "provider-health" | "runtime-status";

/** The operator panel's one line on what the machine is doing (wb#727). */
export interface MachineLine {
  state:
    | "unavailable"
    | "emergency_stop"
    | "stopped"
    | "fault"
    | "tripped"
    | "device_lost"
    | "device_back"
    | "running"
    | "ready";
  text: string;
  target: MachineLineTarget | null;
  linkLabel: string;
}

/**
 * The devices the runtime holds as lost (wb#731): `latched` is every handle
 * the loss latch holds, `lost` those of them unreachable now.
 */
export function deviceLosses(
  status: RuntimeApiStatus,
  stopChain: StopChainStatus | null,
  providers: ProviderHealth[],
): { latched: string[]; lost: string[] } {
  // A device the emergency stop removed power from is the stop, not a loss (wb#631).
  const stopped = new Set(stopChain?.expected_losses ?? []);
  const latched = status.device_loss_latched.filter((handle) => !stopped.has(handle));
  // The latch holds a device until MANUAL -> AUTO, even once it is back, so
  // whether it is lost now comes from provider health.
  const reachable = new Set<string>();
  for (const provider of providers) {
    if (provider.state !== "AVAILABLE") continue;
    for (const device of provider.devices ?? []) {
      if (device.health === "OK" || device.health === "WARNING") {
        reachable.add(`${provider.provider_id}/${device.device_id}`);
      }
    }
  }
  return { latched, lost: latched.filter((handle) => !reachable.has(handle)) };
}

/**
 * Why Start experiment would not work, with the fix, or "" (wb#729). Only
 * what Operate reads exactly: a brake has no declaration yet (anolis#686).
 * FAULT is refused rather than left by way of MANUAL, which would clear the
 * fault before the operator saw its cause.
 */
export function experimentStartRefusal(
  status: RuntimeApiStatus | null,
  estop: EstopStatus | null,
  stopChain: StopChainStatus | null,
  trippedDevices: string[],
  providers: ProviderHealth[],
  faultReason = "",
): string {
  if (!status || String(status.status?.code || "").toUpperCase() !== "OK") {
    return "Refused: the runtime is not answering. See Runtime Status, then Start.";
  }
  if (stopChain?.state === "in_force") {
    return "Refused: the emergency stop is in force. Release it, then Start.";
  }
  if (estop?.latched) {
    return "Refused: the software stop is latched. Clear the latch above, then Start.";
  }
  if (status.mode === "FAULT") {
    const why = faultReason ? ` (${faultReason})` : "";
    return `Refused: the runtime is in FAULT${why}. Set MANUAL under Mode once the cause is fixed, then Start.`;
  }
  if (trippedDevices.length) {
    return `Refused: ${trippedDevices.join(", ")} tripped its command watchdog. Clear the trip under Watchdog trips, then Start.`;
  }
  const { lost } = deviceLosses(status, stopChain, providers);
  if (lost.length) {
    return `Refused: ${lost.join(", ")} lost. Check its power (see Provider Health), then Start.`;
  }
  return "";
}

/**
 * The operator panel's one line on what the machine is doing and what to do
 * next (wb#727). From the status Operate already polls, so it is never staler
 * than the rest of the page. The first state that holds wins, in this order:
 * what blocks every action comes before what one device needs.
 */
export function machineLine(
  status: RuntimeApiStatus | null,
  estop: EstopStatus | null,
  stopChain: StopChainStatus | null,
  trippedDevices: string[],
  providers: ProviderHealth[] = [],
): MachineLine {
  const line = (
    state: MachineLine["state"],
    text: string,
    target: MachineLineTarget | null = null,
    linkLabel = "",
  ): MachineLine => ({ state, text, target, linkLabel });

  if (!status || String(status.status?.code || "").toUpperCase() !== "OK") {
    return line("unavailable", "The runtime is not answering.", "runtime-status", "See Connection");
  }
  if (stopChain?.state === "in_force") {
    return line("emergency_stop", "Emergency stop in force: release it to continue.");
  }
  if (estop?.latched) {
    return line("stopped", "Stopped by the software stop: clear it to continue.");
  }
  if (status.mode === "FAULT") {
    return line(
      "fault",
      "In FAULT: set MANUAL to recover. AUTO is not offered from FAULT, which accepts only MANUAL.",
      "mode",
      "Go to Mode",
    );
  }
  if (trippedDevices.length) {
    return line(
      "tripped",
      `Board tripped: clear the trip on ${trippedDevices.join(", ")}.`,
      "watchdog-trips",
      "Clear the trip",
    );
  }
  const { latched, lost } = deviceLosses(status, stopChain, providers);
  if (lost.length) {
    return line(
      "device_lost",
      `Device lost: check its power (${lost.join(", ")}).`,
      "provider-health",
      "See Provider Health",
    );
  }
  if (latched.length) {
    return line(
      "device_back",
      `${latched.join(", ")} came back after a loss. Automation won't drive them until you go MANUAL → AUTO.`,
      "mode",
      "Go to Mode",
    );
  }
  if (status.mode === "AUTO") return line("running", "Experiment running.");
  return line("ready", "Ready.");
}

/** One of the operator panel's readings (wb#728). */
export interface OperatorReading {
  label: string;
  /** The value with its unit, or "--" when there is none. */
  text: string;
  /** Empty while the reading is good, else what is wrong with it. */
  note: "" | "stale" | "faulted" | "unavailable" | "no reading";
}

function typedNumber(value: unknown): number | null {
  if (typeof value === "number") return value;
  const v = asObject(value);
  const n =
    v.type === "double" ? v.double : v.type === "int64" || v.type === "uint64" ? v[v.type] : null;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

function typedText(value: unknown): string {
  const v = asObject(value);
  if (typeof v.type !== "string")
    return value === null || value === undefined ? "--" : String(value);
  const raw = v[v.type === "bytes" ? "base64" : v.type];
  return raw === undefined ? "--" : String(raw);
}

/**
 * The profile's declared readings, from the state Operate holds per device
 * (wb#728). A reading with declared decimals is rounded to them; without, it
 * reads as the runtime sent it.
 */
export function operatorReadings(
  operator: OperatorBlock | undefined,
  deviceStates: Record<string, DeviceStateValue[]>,
): OperatorReading[] {
  return (operator?.readings ?? []).map((r) => {
    const [providerId, deviceId, signalId] = r.signal.split("/");
    const sig = deviceStates[`${providerId}/${deviceId}`]?.find((v) => v.signal_id === signalId);
    if (!sig) return { label: r.label, text: "--", note: "no reading" };
    const n = typedNumber(sig.value);
    const value =
      n !== null && r.decimals !== undefined ? n.toFixed(r.decimals) : typedText(sig.value);
    const quality = String(sig.quality ?? "OK").toUpperCase();
    const note: OperatorReading["note"] =
      quality === "STALE"
        ? "stale"
        : quality === "FAULT"
          ? "faulted"
          : quality === "UNAVAILABLE"
            ? "unavailable"
            : "";
    return { label: r.label, text: r.unit ? `${value} ${r.unit}` : value, note };
  });
}

/**
 * A line per settings group on what is set now (wb#728), from the declared
 * settings and the parameters' values only. A group whose items include one
 * of experiment.start's enable parameters reads on or off from it: on means
 * scheduled, not that its actuator is running now.
 */
export function operatorSettingsLines(
  operator: OperatorBlock | undefined,
  parameters: ParameterDefinition[],
): string[] {
  const enables = new Set(operator?.experiment?.start?.enable ?? []);
  const byName = new Map(parameters.map((p) => [p.name, p]));
  return (operator?.settings ?? []).map((group) => {
    const parts: string[] = [];
    for (const item of group.items) {
      const param = byName.get(item.parameter);
      if (enables.has(item.parameter)) {
        parts.unshift(param ? (param.value === true ? "on" : "off") : "--");
        continue;
      }
      const value = param ? String(param.value) : "--";
      parts.push(`${item.label} ${value}${item.unit ? ` ${item.unit}` : ""}`);
    }
    return `${group.group}: ${parts.join(", ")}`;
  });
}

/** The "provider/device" a "provider/device/signal" reads from. */
export function signalDevice(signal: string): string {
  return signal.split("/").slice(0, 2).join("/");
}

/**
 * The messages of experiment.start.require entries that do not hold
 * (anolis#686), from the state Operate holds per device. A reading that is
 * missing or not OK cannot show the condition holds, so it does not, and the
 * message says which.
 */
export function unmetStartRequirements(
  require: StartRequirement[],
  deviceStates: Record<string, DeviceStateValue[]>,
): string[] {
  const unmet: string[] = [];
  for (const r of require) {
    const signalId = r.signal.split("/")[2];
    const sig = deviceStates[signalDevice(r.signal)]?.find((v) => v.signal_id === signalId);
    if (!sig) {
      unmet.push(`${r.message} (${r.signal}: no reading)`);
      continue;
    }
    const quality = String(sig.quality ?? "OK").toUpperCase();
    if (quality !== "OK") {
      unmet.push(`${r.message} (${r.signal}: reading is ${quality.toLowerCase()})`);
      continue;
    }
    const v = asObject(sig.value);
    const holds =
      typeof r.equals === "boolean"
        ? (v.type === "bool" ? v.bool : sig.value) === r.equals
        : typeof r.equals === "number"
          ? typedNumber(sig.value) === r.equals
          : typedText(sig.value) === r.equals;
    if (!holds) unmet.push(r.message);
  }
  return unmet;
}

/** Uptime for reading at a glance: the two largest units, "14 h 55 min" (wb#748). */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const units: [number, string][] = [
    [Math.floor(s / 86400), "d"],
    [Math.floor((s % 86400) / 3600), "h"],
    [Math.floor((s % 3600) / 60), "min"],
    [s % 60, "s"],
  ];
  const first = units.findIndex(([n]) => n > 0);
  if (first === -1) return "0 s";
  return units
    .slice(first, first + 2)
    .filter(([n]) => n > 0)
    .map(([n, u]) => `${n} ${u}`)
    .join(" ");
}

/** The event stream's state as a label, a badge class and a detail (wb#748). */
export function summarizeStream(s: {
  state: string;
  attempts: number;
  delay_ms?: number;
  idle_ms?: number;
}): { label: string; cls: string; detail: string } {
  if (s.state === "connected") return { label: "Connected", cls: "ok", detail: "" };
  if (s.state === "reconnecting")
    return {
      label: "Reconnecting",
      cls: "unavailable",
      detail: `attempt ${s.attempts}, retry in ${Math.max(1, Math.ceil((s.delay_ms || 0) / 1000))} s`,
    };
  if (s.state === "stale")
    return {
      label: "Quiet",
      cls: "stale",
      detail: `no events for ${formatDuration((s.idle_ms || 0) / 1000)}`,
    };
  return { label: "Disconnected", cls: "unavailable", detail: "" };
}
