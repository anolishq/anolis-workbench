<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import {
    extractDevices,
    extractProvidersHealth,
    extractCapabilities,
    extractDeviceStateValues,
    extractMode,
    extractRuntimeStatus,
    extractAutomationStatus,
    extractAutomationTree,
    extractParameters,
    normalizeProviderHealthQuality,
    deriveOperateAvailability,
    coerceParameterValue,
    normalizeParameterType,
    renderBtOutline,
  } from "../lib/operate-contracts";
  import {
    createOperateEventStreamManager,
    buildTraceEvent,
    appendEventTrace,
  } from "../lib/operate-events";

  let {
    projectName,
    system,
    runtimeStatus,
  }: {
    projectName: string | null;
    system: Record<string, any> | null;
    runtimeStatus: Record<string, any>;
  } = $props();

  const running = $derived(Boolean(runtimeStatus?.running));
  const runningProject = $derived(
    typeof runtimeStatus?.active_project === "string" ? runtimeStatus.active_project : null,
  );
  const available = $derived(running && runningProject === projectName);

  // ── Operate state ─────────────────────────────────────────────────────────
  let mode = $state<string>("--");
  let modeBadgeClass = $state<string>("unknown");
  let modeSelectorValue = $state<string>("");
  let modeSelectorDirty = $state<boolean>(false);
  let modeFeedback = $state<string>("");
  let modeFeedbackClass = $state<string>("field-note");
  let setModeRunning = $state<boolean>(false);

  let providerHealth = $state<any[]>([]);
  let devices = $state<any[]>([]);
  let selectedKey = $state<string>("");
  let deviceStates = $state<Record<string, any[]>>({});
  let capabilities = $state<Record<string, any>>({});
  let runtimeStatusData = $state<Record<string, any> | null>(null);
  let parameters = $state<any[]>([]);
  let automationStatus = $state<Record<string, any> | null>(null);
  let behaviorTree = $state<string>("");
  let eventTrace = $state<
    Array<{ type: string; timestamp_ms: number; details: string; payload?: any }>
  >([]);

  let streamStatus = $state<{
    state: string;
    attempts: number;
    delay_ms?: number;
    idle_ms?: number;
  }>({
    state: "disconnected",
    attempts: 0,
  });
  let streamManager: ReturnType<typeof createOperateEventStreamManager> | null = null;

  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let telemetryLoaded = $state<boolean>(false);
  let telemetryFrame = $state<HTMLIFrameElement | null>(null);

  const TELEMETRY_URL = "http://localhost:3001";
  const EVENT_TRACE_LIMIT = 100;

  // ── Availability: react to prop changes ───────────────────────────────────
  $effect(() => {
    if (available) {
      startOperate();
    } else {
      stopOperate();
    }
  });

  onDestroy(() => stopOperate());

  // ── Core activate/deactivate ──────────────────────────────────────────────
  function startOperate() {
    if (pollTimer !== null) return; // already running
    ensureTelemetryLoaded();
    void refreshOperate();
    pollTimer = setInterval(() => void refreshOperate(), 5000);
    ensureEventStream();
  }

  function stopOperate() {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    closeEventStream();
    clearOperateData();
  }

  function clearOperateData() {
    devices = [];
    selectedKey = "";
    deviceStates = {};
    capabilities = {};
    parameters = [];
    runtimeStatusData = null;
    automationStatus = null;
    behaviorTree = "";
    modeSelectorDirty = false;
    streamStatus = { state: "disconnected", attempts: 0 };
    mode = "--";
    modeBadgeClass = "unknown";
    providerHealth = [];
    eventTrace = [];
  }

  function ensureTelemetryLoaded() {
    if (!telemetryFrame || telemetryLoaded) return;
    telemetryFrame.src = TELEMETRY_URL;
    telemetryLoaded = true;
  }

  // ── Poll ──────────────────────────────────────────────────────────────────
  async function refreshOperate(): Promise<void> {
    if (!projectName) return;
    let status;
    try {
      status = await fetchJson("/api/status");
    } catch (err) {
      mode = "--";
      modeBadgeClass = "unknown";
      return;
    }
    const avail = deriveOperateAvailability(status, projectName);
    if (!avail.available) {
      mode = "--";
      modeBadgeClass = "unknown";
      clearOperateData();
      return;
    }

    ensureEventStream();
    ensureTelemetryLoaded();

    const [modeRes, phRes, devRes, rtRes, parRes, asRes, atRes] = await Promise.allSettled([
      fetchJson("/v0/mode"),
      fetchJson("/v0/providers/health"),
      fetchJson("/v0/devices"),
      fetchJson("/v0/runtime/status"),
      fetchJson("/v0/parameters"),
      fetchJson("/v0/automation/status"),
      fetchJson("/v0/automation/tree"),
    ]);

    if (modeRes.status === "fulfilled") {
      const m = extractMode(modeRes.value) ?? "UNKNOWN";
      mode = m;
      modeBadgeClass = "ok";
      if (!modeSelectorDirty) modeSelectorValue = m;
    } else {
      mode = "UNKNOWN";
      modeBadgeClass = "unavailable";
    }

    providerHealth = phRes.status === "fulfilled" ? extractProvidersHealth(phRes.value) : [];

    if (devRes.status === "fulfilled") {
      devices = extractDevices(devRes.value);
      if (devices.length && !selectedKey)
        selectedKey = `${devices[0].provider_id}/${devices[0].device_id}`;
      await ensureSelectedDeviceLoaded();
    } else {
      devices = [];
    }

    runtimeStatusData = rtRes.status === "fulfilled" ? extractRuntimeStatus(rtRes.value) : null;
    parameters = parRes.status === "fulfilled" ? extractParameters(parRes.value) : [];
    automationStatus = asRes.status === "fulfilled" ? extractAutomationStatus(asRes.value) : null;
    behaviorTree = atRes.status === "fulfilled" ? extractAutomationTree(atRes.value) : "";
  }

  async function refreshParametersOnly(): Promise<void> {
    try {
      const p = await fetchJson("/v0/parameters");
      parameters = extractParameters(p);
    } catch {
      /* best-effort */
    }
  }
  async function refreshProviderHealthOnly(): Promise<void> {
    try {
      const p = await fetchJson("/v0/providers/health");
      providerHealth = extractProvidersHealth(p);
    } catch {
      /* best-effort */
    }
  }
  async function refreshModeOnly(): Promise<void> {
    try {
      const p = await fetchJson("/v0/mode");
      const m = extractMode(p) ?? "UNKNOWN";
      mode = m;
      modeBadgeClass = "ok";
      if (!modeSelectorDirty) modeSelectorValue = m;
    } catch {
      mode = "UNKNOWN";
      modeBadgeClass = "unavailable";
    }
  }

  // ── Device detail ─────────────────────────────────────────────────────────
  async function ensureSelectedDeviceLoaded(): Promise<void> {
    if (!selectedKey || !devices.length) return;
    const dev = getSelectedDevice();
    if (!dev) return;
    try {
      const vp = await fetchJson(
        `/v0/state/${encodeURIComponent(dev.provider_id)}/${encodeURIComponent(dev.device_id)}`,
      );
      deviceStates = { ...deviceStates, [selectedKey]: extractDeviceStateValues(vp) };
    } catch {
      deviceStates = { ...deviceStates, [selectedKey]: [] };
    }
    if (!capabilities[selectedKey]) {
      try {
        const cp = await fetchJson(
          `/v0/devices/${encodeURIComponent(dev.provider_id)}/${encodeURIComponent(dev.device_id)}/capabilities`,
        );
        capabilities = { ...capabilities, [selectedKey]: extractCapabilities(cp) };
      } catch {
        capabilities = { ...capabilities, [selectedKey]: { functions: [] } };
      }
    }
  }

  function getSelectedDevice(): any | null {
    if (!selectedKey) return null;
    return devices.find((d) => `${d.provider_id}/${d.device_id}` === selectedKey) ?? null;
  }

  async function selectDevice(key: string): Promise<void> {
    selectedKey = key;
    await ensureSelectedDeviceLoaded();
  }

  // ── Mode set ──────────────────────────────────────────────────────────────
  async function setMode(): Promise<void> {
    if (!modeSelectorValue) return;
    setModeRunning = true;
    modeFeedback = "";
    modeFeedbackClass = "field-note";
    try {
      await fetchJson("/v0/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: modeSelectorValue }),
      });
      modeSelectorDirty = false;
      modeFeedback = "";
      await refreshModeOnly();
    } catch (err) {
      modeFeedback = `Failed to set mode: ${err instanceof Error ? err.message : String(err)}`;
      modeFeedbackClass = "field-error";
    } finally {
      setModeRunning = false;
    }
  }

  // ── Function call ──────────────────────────────────────────────────────────
  let functionFeedback = $state<Record<string, { text: string; cls: string }>>({});

  async function callFunction(
    deviceKey: string,
    func: any,
    formData: Record<string, any>,
  ): Promise<void> {
    const fbKey = `${deviceKey}:${func.function_id}`;
    const [providerId, deviceId] = deviceKey.split("/");
    functionFeedback = { ...functionFeedback, [fbKey]: { text: "Executing...", cls: "" } };
    try {
      const argsPayload = buildArgsPayload(func, formData);
      await fetchJson("/v0/call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider_id: providerId,
          device_id: deviceId,
          function_id: func.function_id,
          args: argsPayload,
        }),
      });
      functionFeedback = { ...functionFeedback, [fbKey]: { text: "Success", cls: "success" } };
      setTimeout(() => {
        functionFeedback = { ...functionFeedback, [fbKey]: { text: "", cls: "" } };
      }, 2500);
    } catch (err) {
      functionFeedback = {
        ...functionFeedback,
        [fbKey]: {
          text: `Error: ${err instanceof Error ? err.message : String(err)}`,
          cls: "error",
        },
      };
    }
  }

  function buildArgsPayload(func: any, formData: Record<string, any>): Record<string, any> {
    const INT64_MIN = -9223372036854775808n,
      INT64_MAX = 9223372036854775807n;
    const JS_SAFE_MIN = BigInt(Number.MIN_SAFE_INTEGER),
      JS_SAFE_MAX = BigInt(Number.MAX_SAFE_INTEGER);
    const args = {};
    for (const [i, argDef] of (func.args ?? []).entries()) {
      const argType = argDef.type || "string";
      const raw = formData[`arg_${i}`];
      if (argType === "bool") {
        args[argDef.name] = { type: "bool", bool: Boolean(raw) };
        continue;
      }
      const value = typeof raw === "string" ? raw.trim() : "";
      if (value === "") {
        if (argDef.required !== false) throw new Error(`Missing required argument: ${argDef.name}`);
        continue;
      }
      if (argType === "double") {
        const n = Number(value);
        if (isNaN(n)) throw new Error(`Invalid double: ${argDef.name}`);
        args[argDef.name] = { type: "double", double: n };
        continue;
      }
      if (argType === "int64") {
        let n;
        try {
          n = BigInt(value);
        } catch {
          throw new Error(`Invalid int64: ${argDef.name}`);
        }
        if (n < INT64_MIN || n > INT64_MAX) throw new Error(`Out-of-range int64: ${argDef.name}`);
        if (n < JS_SAFE_MIN || n > JS_SAFE_MAX)
          throw new Error(`int64 out of browser-safe range: ${argDef.name}`);
        args[argDef.name] = { type: "int64", int64: Number(n) };
        continue;
      }
      if (argType === "uint64") {
        const UINT64_MAX = 18446744073709551615n;
        let n;
        try {
          n = BigInt(value);
        } catch {
          throw new Error(`Invalid uint64: ${argDef.name}`);
        }
        if (n < 0n || n > UINT64_MAX) throw new Error(`Out-of-range uint64: ${argDef.name}`);
        if (n > JS_SAFE_MAX) throw new Error(`uint64 out of browser-safe range: ${argDef.name}`);
        args[argDef.name] = { type: "uint64", uint64: Number(n) };
        continue;
      }
      if (argType === "bytes") {
        args[argDef.name] = { type: "bytes", base64: value };
        continue;
      }
      args[argDef.name] = { type: "string", string: value };
    }
    return args;
  }

  // ── Parameter update ──────────────────────────────────────────────────────
  let paramFeedback = $state<Record<string, { text: string; cls: string }>>({});

  async function updateParameter(param: any, rawValue: unknown): Promise<void> {
    const type = normalizeParameterType(param.type);
    paramFeedback = { ...paramFeedback, [param.name]: { text: "", cls: "" } };
    try {
      const value = coerceParameterValue({
        type,
        rawValue,
        min: param.min,
        max: param.max,
        allowedValues: param.allowed_values,
      });
      await fetchJson("/v0/parameters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: param.name, value }),
      });
      paramFeedback = { ...paramFeedback, [param.name]: { text: "Updated", cls: "success" } };
      setTimeout(() => {
        paramFeedback = { ...paramFeedback, [param.name]: { text: "", cls: "" } };
      }, 2000);
      await refreshParametersOnly();
    } catch (err) {
      paramFeedback = {
        ...paramFeedback,
        [param.name]: {
          text: `Error: ${err instanceof Error ? err.message : String(err)}`,
          cls: "error",
        },
      };
    }
  }

  // ── SSE Event stream ──────────────────────────────────────────────────────
  function ensureEventStream() {
    if (streamManager) return;
    streamManager = createOperateEventStreamManager({
      url: "/v0/events",
      onEvent: handleSseEvent,
      onConnectionStatus: (s) => {
        const prev = streamStatus.state;
        streamStatus = s;
        if (s.state !== prev) {
          const details =
            s.state === "reconnecting"
              ? `Reconnecting in ${Math.max(1, Math.ceil((s.delay_ms || 0) / 1000))}s`
              : s.state === "stale"
                ? `Stale for ${Math.floor((s.idle_ms || 0) / 1000)}s`
                : s.state === "connected"
                  ? "Connected"
                  : "Disconnected";
          addTraceEvent("stream_status", { timestamp_ms: Date.now() }, details);
        }
      },
      onParseError: (err, et) =>
        addTraceEvent(
          "stream_error",
          { timestamp_ms: Date.now() },
          `${et}: ${err instanceof Error ? err.message : String(err)}`,
        ),
    });
    streamManager.connect();
  }

  function closeEventStream() {
    if (streamManager) {
      streamManager.disconnect();
      streamManager = null;
    }
    streamStatus = { state: "disconnected", attempts: 0 };
  }

  function handleSseEvent(eventType: string, payload: Record<string, any>): void {
    if (eventType === "state_update") consumeStateEvent(payload);
    else if (eventType === "quality_change") consumeQualityEvent(payload);
    else if (eventType === "mode_change") consumeModeChangeEvent(payload);
    else if (eventType === "parameter_change") void refreshParametersOnly();
    else if (eventType === "bt_error") consumeBtErrorEvent(payload);
    else if (eventType === "provider_health_change") void refreshProviderHealthOnly();
    addTraceEvent(eventType, payload);
  }

  function addTraceEvent(
    type: string,
    payload: Record<string, any>,
    detailsOverride: string | null = null,
  ): void {
    const ev = buildTraceEvent(type, payload, Date.now());
    if (detailsOverride) ev.details = detailsOverride;
    const buf = [...eventTrace];
    appendEventTrace(buf, ev, EVENT_TRACE_LIMIT);
    eventTrace = buf;
  }

  function consumeStateEvent(payload: Record<string, any>): void {
    const key = `${payload.provider_id}/${payload.device_id}`;
    const values = [...(deviceStates[key] ?? [])];
    const idx = values.findIndex((e) => e.signal_id === payload.signal_id);
    const normalized = {
      ...payload,
      timestamp_ms: Number(payload.timestamp_ms) || Number(payload.timestamp_epoch_ms) || 0,
    };
    if (idx >= 0) values[idx] = normalized;
    else values.push(normalized);
    deviceStates = { ...deviceStates, [key]: values };
  }

  function consumeQualityEvent(payload: Record<string, any>): void {
    const key = `${payload.provider_id}/${payload.device_id}`;
    const values = deviceStates[key];
    if (!values) return;
    const updated = values.map((e) =>
      e.signal_id === payload.signal_id ? { ...e, quality: payload.new_quality } : e,
    );
    deviceStates = { ...deviceStates, [key]: updated };
  }

  function consumeModeChangeEvent(payload: Record<string, any>): void {
    const m = typeof payload.new_mode === "string" ? payload.new_mode : "UNKNOWN";
    mode = m;
    modeBadgeClass = "ok";
    if (!modeSelectorDirty) modeSelectorValue = m;
  }

  function consumeBtErrorEvent(payload: Record<string, any>): void {
    if (!automationStatus) {
      automationStatus = {
        enabled: true,
        active: true,
        bt_status: "ERROR",
        last_tick_ms: Number(payload.timestamp_ms) || 0,
        ticks_since_progress: 0,
        total_ticks: 0,
        last_error: String(payload.error || "Unknown behavior-tree error"),
        error_count: 1,
        current_tree: "",
      };
    } else {
      automationStatus = {
        ...automationStatus,
        bt_status: "ERROR",
        last_error: String(payload.error || "Unknown BT error"),
        error_count: (automationStatus.error_count || 0) + 1,
      };
    }
  }

  // ── Formatters ────────────────────────────────────────────────────────────
  function formatValue(v: any): string {
    if (v === null || v === undefined) return "--";
    if (typeof v === "object") {
      if (typeof v.type === "string") {
        if (v.type === "double" && v.double !== undefined) return String(v.double);
        if (v.type === "int64" && v.int64 !== undefined) return String(v.int64);
        if (v.type === "uint64" && v.uint64 !== undefined) return String(v.uint64);
        if (v.type === "bool" && v.bool !== undefined) return v.bool ? "true" : "false";
        if (v.type === "string" && v.string !== undefined) return String(v.string);
        if (v.type === "bytes" && v.base64 !== undefined) return String(v.base64);
      }
      return JSON.stringify(v);
    }
    return String(v);
  }

  function formatBtOutline(tree: string): string {
    if (!tree || !tree.trim()) return "No behavior tree loaded.";
    try {
      const parser = new DOMParser();
      const xmlDoc = parser.parseFromString(tree, "text/xml");
      if (xmlDoc.querySelector("parsererror")) return tree;
      return renderBtOutline(xmlDoc);
    } catch {
      return tree;
    }
  }

  // Function form state keyed by deviceKey:functionId
  let funcFormData = $state<Record<string, Record<string, any>>>({});
  function getFuncFormKey(deviceKey: string, func: any): string {
    return `${deviceKey}:${func.function_id}`;
  }
  function getFuncFormData(deviceKey: string, func: any): Record<string, any> {
    return funcFormData[getFuncFormKey(deviceKey, func)] ?? {};
  }
  function setFuncArg(deviceKey: string, func: any, argIdx: number, value: any): void {
    const k = getFuncFormKey(deviceKey, func);
    funcFormData = {
      ...funcFormData,
      [k]: { ...(funcFormData[k] ?? {}), [`arg_${argIdx}`]: value },
    };
  }

  // Parameter input state
  let paramInputs = $state<Record<string, string>>({});
  function getParamInput(name: string): string {
    return paramInputs[name] ?? "";
  }
  function setParamInput(name: string, value: string): void {
    paramInputs = { ...paramInputs, [name]: value };
  }
</script>

<section id="workspace-operate" class="workspace visible">
  <div class="workspace-header">
    <h2>Operate</h2>
  </div>

  {#if !available}
    <div class="workspace-advisory">
      {#if running && runningProject}
        Runtime is running for "{runningProject}", not this project.
      {:else}
        Runtime is not running for this project.
      {/if}
    </div>
  {:else}
    <!-- Mode control -->
    <div class="operate-section operate-mode-section">
      <h3>Mode</h3>
      <div class="mode-row">
        <span>Current: <span class="badge {modeBadgeClass}">{mode}</span></span>
        <select
          value={modeSelectorValue}
          onchange={(e: any) => {
            modeSelectorValue = e.target.value;
            modeSelectorDirty = true;
          }}
        >
          <option value="ACTIVE">ACTIVE</option>
          <option value="IDLE">IDLE</option>
          <option value="SAFE">SAFE</option>
        </select>
        <button
          type="button"
          class="btn-secondary btn-sm"
          disabled={setModeRunning}
          onclick={setMode}
        >
          {setModeRunning ? "Setting…" : "Set Mode"}
        </button>
        {#if modeFeedback}
          <span class={modeFeedbackClass}>{modeFeedback}</span>
        {/if}
      </div>
    </div>

    <!-- Runtime status -->
    <div class="operate-section">
      <h3>Runtime Status</h3>
      {#if runtimeStatusData}
        <span
          class="badge {String(runtimeStatusData.status?.code || '').toUpperCase() === 'OK'
            ? 'ok'
            : 'unavailable'}"
        >
          {String(runtimeStatusData.status?.code || "").toUpperCase() === "OK"
            ? "OK"
            : "UNAVAILABLE"}
        </span>
        <span class="operate-meta">
          Mode {runtimeStatusData.mode} | Devices {runtimeStatusData.device_count} | Poll {runtimeStatusData.polling_interval_ms}ms
          | Uptime {runtimeStatusData.uptime_seconds}s
        </span>
      {:else}
        <span class="badge unavailable">UNAVAILABLE</span>
        <span class="operate-meta">Runtime status unavailable.</span>
      {/if}
    </div>

    <!-- Event stream status -->
    <div class="operate-section">
      <h3>Event Stream</h3>
      {#if streamStatus.state === "connected"}
        <span class="badge ok">CONNECTED</span> <span class="operate-meta">SSE stream active.</span>
      {:else if streamStatus.state === "reconnecting"}
        <span class="badge unavailable">RECONNECTING</span>
        <span class="operate-meta"
          >Attempt {streamStatus.attempts}, retry in {Math.max(
            1,
            Math.ceil((streamStatus.delay_ms || 0) / 1000),
          )}s.</span
        >
      {:else if streamStatus.state === "stale"}
        <span class="badge stale">STALE</span>
        <span class="operate-meta"
          >No events for {Math.floor((streamStatus.idle_ms || 0) / 1000)}s.</span
        >
      {:else}
        <span class="badge unavailable">DISCONNECTED</span>
        <span class="operate-meta">Stream disconnected.</span>
      {/if}
    </div>

    <!-- Provider health -->
    <div class="operate-section">
      <h3>Provider Health</h3>
      <ul class="operate-provider-health">
        {#if providerHealth.length === 0}
          <li class="placeholder">No provider health available.</li>
        {:else}
          {#each providerHealth as entry}
            {@const pid = typeof entry?.provider_id === "string" ? entry.provider_id : "unknown"}
            {@const q = normalizeProviderHealthQuality(
              entry?.health?.quality || entry?.state || "UNKNOWN",
            )}
            {@const lc = typeof entry?.lifecycle_state === "string" ? entry.lifecycle_state : "--"}
            {@const dc = Number.isFinite(Number(entry?.device_count))
              ? Number(entry.device_count)
              : 0}
            <li>
              {pid} <span class="badge {q.toLowerCase()}">{q}</span>
              <div class="provider-row-meta">{lc} | {dc} device(s)</div>
            </li>
          {/each}
        {/if}
      </ul>
    </div>

    <!-- Devices -->
    <div class="operate-section operate-devices-layout">
      <div class="operate-device-list-col">
        <h3>Devices</h3>
        <ul class="operate-device-list">
          {#if devices.length === 0}
            <li class="placeholder">No devices found.</li>
          {:else}
            {#each devices as device}
              {@const key = `${device.provider_id}/${device.device_id}`}
              <li class:selected={key === selectedKey}>
                <button
                  type="button"
                  class="operate-device-select"
                  onclick={() => selectDevice(key)}
                >
                  {device.display_name || device.device_id || key} ({key})
                </button>
              </li>
            {/each}
          {/if}
        </ul>
      </div>

      <div class="operate-device-detail-col">
        <h3 id="device-title">
          {#if selectedKey && getSelectedDevice()}
            {getSelectedDevice()?.display_name || getSelectedDevice()?.device_id} ({selectedKey})
          {:else}
            Device Detail
          {/if}
        </h3>

        {#if !selectedKey || !getSelectedDevice()}
          <p class="placeholder">Select a device to inspect signals and execute functions.</p>
        {:else}
          {@const signals = deviceStates[selectedKey] ?? []}
          {@const caps = capabilities[selectedKey] ?? { functions: [] }}

          {#if signals.length === 0}
            <p class="placeholder">No signals available.</p>
          {:else}
            <table class="signal-table">
              <thead><tr><th>Signal</th><th>Value</th><th>Quality</th><th>Timestamp</th></tr></thead
              >
              <tbody>
                {#each signals as sig}
                  <tr>
                    <td>{sig.signal_id || "-"}</td>
                    <td>{formatValue(sig.value)}</td>
                    <td>{sig.quality ?? "--"}</td>
                    <td
                      >{sig.timestamp_ms > 0
                        ? new Date(sig.timestamp_ms).toLocaleTimeString()
                        : "--"}</td
                    >
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}

          {#if caps.functions?.length === 0}
            <p class="placeholder">No callable functions declared.</p>
          {:else}
            {#each caps.functions as func}
              {@const fbKey = `${selectedKey}:${func.function_id}`}
              <div class="function-card">
                <h4>{func.display_name || func.name || `Function ${func.function_id}`}</h4>
                {#if func.description}
                  <p class="function-description">{func.description}</p>
                {/if}
                <form
                  onsubmit={(e: any) => {
                    e.preventDefault();
                    void callFunction(selectedKey, func, getFuncFormData(selectedKey, func));
                  }}
                >
                  {#each func.args ?? [] as arg, ai}
                    <div class="arg-row">
                      <label for="fn-{fbKey}-{ai}">
                        {arg.name}{arg.required !== false ? " *" : ""}
                        {#if arg.min !== undefined && arg.max !== undefined}
                          <span class="constraint-hint">[{arg.min} - {arg.max}]</span>
                        {/if}
                      </label>
                      {#if arg.type === "bool"}
                        <input
                          id="fn-{fbKey}-{ai}"
                          type="checkbox"
                          checked={Boolean(getFuncFormData(selectedKey, func)[`arg_${ai}`])}
                          onchange={(e: any) => setFuncArg(selectedKey, func, ai, e.target.checked)}
                        />
                      {:else if arg.type === "double"}
                        <input
                          id="fn-{fbKey}-{ai}"
                          type="number"
                          step="any"
                          value={getFuncFormData(selectedKey, func)[`arg_${ai}`] ?? ""}
                          oninput={(e: any) => setFuncArg(selectedKey, func, ai, e.target.value)}
                        />
                      {:else}
                        <input
                          id="fn-{fbKey}-{ai}"
                          type="text"
                          inputmode={arg.type === "int64" || arg.type === "uint64"
                            ? "numeric"
                            : "text"}
                          placeholder={arg.type === "bytes" ? "Base64 encoded" : ""}
                          value={getFuncFormData(selectedKey, func)[`arg_${ai}`] ?? ""}
                          oninput={(e: any) => setFuncArg(selectedKey, func, ai, e.target.value)}
                        />
                      {/if}
                    </div>
                  {/each}
                  <div class="function-actions">
                    <button type="submit" class="btn-secondary btn-sm">Execute</button>
                    {#if functionFeedback[fbKey]?.text}
                      <span class="function-feedback {functionFeedback[fbKey].cls}"
                        >{functionFeedback[fbKey].text}</span
                      >
                    {/if}
                  </div>
                </form>
              </div>
            {/each}
          {/if}
        {/if}
      </div>
    </div>

    <!-- Parameters -->
    <div class="operate-section">
      <h3>Parameters</h3>
      {#if parameters.length === 0}
        <p class="placeholder">No parameters available.</p>
      {:else}
        <div class="parameters-grid">
          {#each parameters as param}
            {@const ptype = normalizeParameterType(param.type)}
            <div class="parameter-card">
              <div class="parameter-header">
                <span class="parameter-name">{param.name}</span>
                <span class="parameter-type">{ptype ?? String(param.type)}</span>
              </div>
              <div class="parameter-value"><strong>{String(param.value)}</strong></div>
              {#if param.min !== undefined || param.max !== undefined}
                <div class="parameter-range">
                  Range: [{param.min ?? "-inf"}, {param.max ?? "+inf"}]
                </div>
              {/if}
              {#if ptype}
                <div class="parameter-controls">
                  {#if ptype === "bool"}
                    <select
                      value={getParamInput(param.name) || String(param.value).toLowerCase()}
                      onchange={(e: any) => setParamInput(param.name, e.target.value)}
                    >
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  {:else if ptype === "string" && Array.isArray(param.allowed_values) && param.allowed_values.length > 0}
                    <select
                      value={getParamInput(param.name) || String(param.value)}
                      onchange={(e: any) => setParamInput(param.name, e.target.value)}
                    >
                      {#each param.allowed_values as av}
                        <option value={String(av)}>{String(av)}</option>
                      {/each}
                    </select>
                  {:else if ptype === "double"}
                    <input
                      type="number"
                      step="any"
                      placeholder="New value"
                      value={getParamInput(param.name)}
                      oninput={(e: any) => setParamInput(param.name, e.target.value)}
                    />
                  {:else}
                    <input
                      type="text"
                      placeholder="New value"
                      value={getParamInput(param.name)}
                      oninput={(e: any) => setParamInput(param.name, e.target.value)}
                    />
                  {/if}
                  <button
                    type="button"
                    class="btn-secondary btn-sm"
                    onclick={() => updateParameter(param, getParamInput(param.name))}>Set</button
                  >
                  {#if paramFeedback[param.name]?.text}
                    <span class="feedback {paramFeedback[param.name].cls}"
                      >{paramFeedback[param.name].text}</span
                    >
                  {/if}
                </div>
              {:else}
                <span class="feedback error">Unsupported parameter type: {String(param.type)}</span>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Automation status -->
    <div class="operate-section">
      <h3>Automation</h3>
      {#if !automationStatus}
        <p class="placeholder">Automation status unavailable.</p>
      {:else}
        {@const btStatus = String(automationStatus.bt_status || "UNKNOWN").toUpperCase()}
        <div class="automation-status-grid">
          <span>Status: <span class="badge bt-{btStatus.toLowerCase()}">{btStatus}</span></span>
          <span>Enabled: {automationStatus.enabled ? "true" : "false"}</span>
          <span>Active: {automationStatus.active ? "true" : "false"}</span>
          <span>Total ticks: {automationStatus.total_ticks}</span>
          <span>Stalled ticks: {automationStatus.ticks_since_progress}</span>
          <span>Error count: {automationStatus.error_count}</span>
          <span>Current tree: {automationStatus.current_tree || "--"}</span>
          {#if automationStatus.last_error}
            <span
              class="error-text {btStatus === 'ERROR'
                ? 'alarm'
                : btStatus === 'STALLED'
                  ? 'warning'
                  : ''}"
            >
              Last error: {automationStatus.last_error}
            </span>
          {/if}
        </div>
        <pre class="behavior-tree-viewer">{formatBtOutline(behaviorTree)}</pre>
      {/if}
    </div>

    <!-- Event trace -->
    <div class="operate-section">
      <h3>Event Trace</h3>
      {#if eventTrace.length === 0}
        <p class="placeholder">No events yet.</p>
      {:else}
        <div class="event-items">
          {#each [...eventTrace].reverse() as ev}
            {@const ts = Number.isFinite(Number(ev.timestamp_ms))
              ? new Date(Number(ev.timestamp_ms)).toLocaleTimeString()
              : "--"}
            {@const typeClass = String(ev.type || "event").replaceAll("_", "-")}
            <div class="event-item {typeClass}">
              <span class="event-time">{ts}</span>
              <span class="event-type">{ev.type}</span>
              <span class="event-details">{ev.details}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Telemetry iframe -->
    <div class="operate-section">
      <h3>Telemetry</h3>
      <div class="telemetry-bar">
        <a href={TELEMETRY_URL} target="_blank" rel="noopener noreferrer"
          >Open Grafana in new tab →</a
        >
      </div>
      <iframe
        bind:this={telemetryFrame}
        title="Telemetry"
        class="telemetry-frame"
        frameborder="0"
        allowfullscreen
      ></iframe>
    </div>
  {/if}
</section>
