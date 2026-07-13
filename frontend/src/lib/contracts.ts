export type UnknownRecord = Record<string, unknown>;

export interface ValidationErrorItem extends UnknownRecord {
  source: string;
  code: string;
  path: string;
  message: string;
}

export interface ApiErrorResponse extends UnknownRecord {
  error: string;
  code?: string;
  errors?: ValidationErrorItem[];
}

export interface ProjectSummary extends UnknownRecord {
  name: string;
  meta: UnknownRecord;
}

export interface TemplateSummary extends UnknownRecord {
  id: string;
  meta: UnknownRecord;
}

export interface RuntimeStatus extends UnknownRecord {
  version?: number;
  active_project: string | null;
  running: boolean;
  pid?: number | null;
  composer?: {
    host: string;
    port: number;
  } & UnknownRecord;
  workbench?: {
    version: number;
  } & UnknownRecord;
}

export interface WorkbenchConfig extends UnknownRecord {
  telemetry_url?: string;
}

export interface PreflightCheck extends UnknownRecord {
  name: string;
  ok: boolean | null;
  error?: string | null;
  hint?: string | null;
  note?: string | null;
}

export interface PreflightResult extends UnknownRecord {
  ok: boolean;
  checks: PreflightCheck[];
}

export interface ProviderCatalogEntry extends UnknownRecord {
  kind: string;
  display_name?: string;
}

export interface ProviderCatalog extends UnknownRecord {
  providers?: ProviderCatalogEntry[];
}

export type ProviderKind = "sim" | "bread" | "ezo" | string;

export interface RestartPolicy extends UnknownRecord {
  enabled?: boolean;
  max_attempts?: number;
  backoff_ms?: number[];
  timeout_ms?: number;
  success_reset_ms?: number;
}

export interface ProviderRuntimeEntry extends UnknownRecord {
  id: string;
  kind: ProviderKind;
  timeout_ms?: number;
  hello_timeout_ms?: number;
  ready_timeout_ms?: number;
  restart_policy?: RestartPolicy;
}

export interface RuntimeTelemetryInflux extends UnknownRecord {
  url?: string;
  org?: string;
  bucket?: string;
  token?: string;
  batch_size?: number;
  flush_interval_ms?: number;
}

export interface RuntimeTelemetry extends UnknownRecord {
  enabled?: boolean;
  influxdb?: RuntimeTelemetryInflux;
}

export interface RuntimeConfig extends UnknownRecord {
  name?: string;
  http_port?: number;
  http_bind?: string;
  cors_origins?: string[];
  cors_allow_credentials?: boolean;
  shutdown_timeout_ms?: number;
  startup_timeout_ms?: number;
  polling_interval_ms?: number;
  log_level?: string;
  telemetry_enabled?: boolean;
  telemetry?: RuntimeTelemetry;
  automation_enabled?: boolean;
  behavior_tree_path?: string | null;
  providers?: ProviderRuntimeEntry[];
}

export interface SimDeviceConfig extends UnknownRecord {
  id: string;
  type: string;
}

export interface BreadDeviceConfig extends UnknownRecord {
  id: string;
  type: string;
  address: string;
}

export interface EzoDeviceConfig extends UnknownRecord {
  id: string;
  type: string;
  address: string;
}

export interface SimProviderConfig extends UnknownRecord {
  kind: "sim";
  startup_policy?: string;
  simulation_mode?: string;
  tick_rate_hz?: number;
  devices?: SimDeviceConfig[];
}

export interface BreadProviderConfig extends UnknownRecord {
  kind: "bread";
  provider_name?: string;
  query_delay_us?: number;
  timeout_ms?: number;
  retry_count?: number;
  discovery?: {
    mode?: string;
    addresses?: string[];
  };
  devices?: BreadDeviceConfig[];
}

export interface EzoProviderConfig extends UnknownRecord {
  kind: "ezo";
  provider_name?: string;
  query_delay_us?: number;
  timeout_ms?: number;
  retry_count?: number;
  devices?: EzoDeviceConfig[];
}

export type ProviderTopologyConfig =
  SimProviderConfig | BreadProviderConfig | EzoProviderConfig | UnknownRecord;

export interface TopologyConfig extends UnknownRecord {
  runtime: RuntimeConfig;
  providers?: Record<string, ProviderTopologyConfig>;
}

export interface ProviderPaths extends UnknownRecord {
  executable?: string;
  bus_path?: string;
}

export interface PathsConfig extends UnknownRecord {
  runtime_executable?: string;
  providers?: Record<string, ProviderPaths>;
}

export interface SystemMeta extends UnknownRecord {
  name: string;
  created: string;
  template: string;
}

export interface SystemConfig extends UnknownRecord {
  meta: SystemMeta;
  topology: TopologyConfig;
  paths: PathsConfig;
}

export interface RuntimeStatusCode extends UnknownRecord {
  code?: string;
  message?: string;
}

export interface RuntimeStatusProvider {
  provider_id: string;
  state: string;
  device_count: number;
}

export interface RuntimeApiStatus extends UnknownRecord {
  status: RuntimeStatusCode;
  mode: string;
  uptime_seconds: number;
  polling_interval_ms: number;
  providers: RuntimeStatusProvider[];
  device_count: number;
}

export interface TypedValue extends UnknownRecord {
  type: "double" | "int64" | "uint64" | "bool" | "string" | "bytes";
  double?: number;
  int64?: number;
  uint64?: number;
  bool?: boolean;
  string?: string;
  base64?: string;
}

export interface DeviceStateValue extends UnknownRecord {
  signal_id: string;
  value: TypedValue | UnknownRecord | string | number | boolean | null;
  timestamp_ms?: number;
  timestamp_epoch_ms?: number;
  quality?: string;
  age_ms?: number;
}

export interface Device extends UnknownRecord {
  provider_id: string;
  device_id: string;
  display_name: string;
  type: string;
}

export interface FunctionArgSpec extends UnknownRecord {
  name: string;
  type: string;
  required: boolean;
  min?: number | string;
  max?: number | string;
  allowed_values?: Array<string | number>;
}

export interface FunctionSpec extends UnknownRecord {
  function_id: number;
  name: string;
  function_name: string;
  display_name: string;
  label: string;
  description: string;
  args: FunctionArgSpec[];
}

export interface DeviceCapabilities extends UnknownRecord {
  signals: UnknownRecord[];
  functions: FunctionSpec[];
}

export interface ParameterDefinition extends UnknownRecord {
  name: string;
  type: "double" | "int64" | "bool" | "string" | string;
  value: string | number | boolean;
  min?: number;
  max?: number;
  allowed_values?: string[];
}

export type ExecutionStatus = "idle" | "running" | "blocked" | "failed" | "completed" | "unknown";

/** Identity of the loaded automation definition (engine-neutral provenance). */
export interface AutomationVersion extends UnknownRecord {
  engine_kind: string;
  id: string;
  digest: string;
  digest_scope: string;
}

/**
 * Engine-neutral automation status (anolis >= v0.1.24).
 *
 * The workbench reads only the neutral contract. The deprecated behaviour-tree
 * mirrors (`bt_status`, `total_ticks`, `current_tree`, …) are intentionally not
 * modelled here and are removed from the runtime in anolis >= v0.1.26.
 */
export interface AutomationStatus extends UnknownRecord {
  execution_status: ExecutionStatus;
  execution_reason: string | null;
  automation_version: AutomationVersion | null;
  last_evaluation_at_epoch_ms: number | null;
  run_id: string | null;
  last_error: string | null;
}

export interface ProviderSupervision extends UnknownRecord {
  enabled: boolean;
  attempt_count: number;
  max_attempts: number;
  crash_detected: boolean;
  circuit_open: boolean;
  next_restart_in_ms: number | null;
}

export interface ProviderDeviceHealth extends UnknownRecord {
  device_id: string;
  health: string;
  last_poll_ms: number;
  staleness_ms: number;
}

export interface ProviderHealth extends UnknownRecord {
  provider_id: string;
  state: string;
  lifecycle_state: string;
  last_seen_ago_ms: number | null;
  uptime_seconds: number;
  device_count: number;
  supervision: ProviderSupervision;
  devices: ProviderDeviceHealth[];
}
