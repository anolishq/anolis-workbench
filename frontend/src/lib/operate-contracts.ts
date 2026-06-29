// operate-contracts.ts — data extraction / normalization for the Operate workspace

import type {
  AutomationStatus,
  AutomationVersion,
  Device,
  DeviceCapabilities,
  DeviceStateValue,
  ExecutionStatus,
  FunctionArgSpec,
  FunctionSpec,
  ParameterDefinition,
  ProviderHealth,
  RuntimeApiStatus,
  UnknownRecord,
} from "./contracts";

/**
 * Runtime modes accepted by `POST /v0/mode` and reported by `GET /v0/mode`.
 * MUST stay in sync with the `RuntimeMode` enum in the vendored runtime-http
 * OpenAPI contract — a unit test guards against drift.
 */
export const RUNTIME_MODES = ["MANUAL", "AUTO", "IDLE", "FAULT"] as const;

export type RuntimeMode = (typeof RUNTIME_MODES)[number];

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
    mode: typeof r.mode === "string" ? r.mode : "UNKNOWN",
    uptime_seconds: toFinite(r.uptime_seconds) ?? 0,
    polling_interval_ms: toFinite(r.polling_interval_ms) ?? 0,
    device_count: toFinite(r.device_count) ?? 0,
    providers: asArray(r.providers) as RuntimeApiStatus["providers"],
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
  };
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
