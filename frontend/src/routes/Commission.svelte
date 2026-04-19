<script lang="ts">
  import { onDestroy } from "svelte";
  import {
    ApiResponseError,
    downloadBlob,
    fetchJson,
    fetchResponse,
    filenameFromContentDisposition,
  } from "../lib/api";
  import type {
    PreflightResult,
    ProviderHealth,
    RuntimeApiStatus,
    RuntimeStatus,
    SystemConfig,
  } from "../lib/contracts";

  let {
    projectName,
    system,
    runtimeStatus,
    commissionRunningForCurrent,
  }: {
    projectName: string | null;
    system: SystemConfig | null;
    runtimeStatus: RuntimeStatus | null;
    commissionRunningForCurrent: boolean;
  } = $props();

  const running = $derived(Boolean(runtimeStatus?.running));
  const runningProject = $derived(
    typeof runtimeStatus?.active_project === "string" ? runtimeStatus.active_project : null,
  );
  const runningForOther = $derived(
    running && Boolean(runningProject) && runningProject !== projectName,
  );

  // ── Launch state ───────────────────────────────────────────────────────────
  let preflightResults = $state<PreflightResult | null>(null);
  let preflightRunning = $state<boolean>(false);
  let launchRunning = $state<boolean>(false);
  let stopRunning = $state<boolean>(false);
  let restartRunning = $state<boolean>(false);
  let actionError = $state<string>("");

  // ── Health badge ──────────────────────────────────────────────────────────
  let healthStatus = $state<"starting" | "healthy" | "unavailable">("starting");
  let healthTimer = $state<ReturnType<typeof setInterval> | null>(null);
  let healthStartedAt = $state<number>(0);

  // ── Log pane ──────────────────────────────────────────────────────────────
  let logLines = $state<string[]>([]);
  let logVisible = $state<boolean>(false);
  let logAutoScroll = true;
  let logEventSource: EventSource | null = null;
  let logEl = $state<HTMLDivElement | null>(null);

  // ── Commission health panel ───────────────────────────────────────────────
  let commissionRuntimeStatus = $state<RuntimeApiStatus | null>(null);
  let commissionProviderHealth = $state<ProviderHealth[]>([]);
  let commissionHealthError = $state<string>("");
  let commissionHealthTimer = $state<ReturnType<typeof setInterval> | null>(null);

  // ── Export ────────────────────────────────────────────────────────────────
  let exportRunning = $state<boolean>(false);
  let exportFeedback = $state<string>("");
  let exportIsError = $state<boolean>(false);

  // ── Operator UI ──────────────────────────────────────────────────────────
  const operatorUiBase = $derived(() => {
    const explicit = system?.topology?.runtime?.operator_ui_base;
    if (typeof explicit === "string" && explicit.trim()) return explicit.trim().replace(/\/$/, "");
    const fromGlobal = window.__ANOLIS_COMPOSER__?.operatorUiBase;
    if (typeof fromGlobal === "string" && fromGlobal.trim())
      return fromGlobal.trim().replace(/\/$/, "");
    return "";
  });
  const runtimeApiBase = $derived(() => {
    const bind = system?.topology?.runtime?.http_bind;
    const port = system?.topology?.runtime?.http_port;
    if (typeof bind !== "string" || bind.trim() === "") return "";
    if (!Number.isFinite(Number(port))) return "";
    return `http://${bind.trim()}:${Number(port)}`;
  });
  const operatorUiLink = $derived(() => {
    if (!operatorUiBase()) return "";
    if (!runtimeApiBase()) return "";
    return `${operatorUiBase()}?api=${encodeURIComponent(runtimeApiBase())}`;
  });

  let commissionLiveDataActive = false;

  // ── Reactive: sync running state when commissionRunningForCurrent changes ─
  $effect(() => {
    if (commissionRunningForCurrent && !commissionLiveDataActive) {
      startHealthPolling();
      connectLogs();
      startCommissionHealthPolling();
      commissionLiveDataActive = true;
    } else if (!commissionRunningForCurrent && commissionLiveDataActive) {
      stopHealthPolling();
      disconnectLogs();
      stopCommissionHealthPolling();
      commissionLiveDataActive = false;
    }
  });

  onDestroy(() => {
    if (commissionLiveDataActive) {
      stopHealthPolling();
      disconnectLogs();
      stopCommissionHealthPolling();
      commissionLiveDataActive = false;
    }
  });

  // ── Health polling ────────────────────────────────────────────────────────
  function startHealthPolling() {
    stopHealthPolling();
    healthStartedAt = Date.now();
    healthStatus = "starting";
    healthTimer = setInterval(async () => {
      if (Date.now() - healthStartedAt < 10_000) {
        healthStatus = "starting";
        return;
      }
      try {
        await fetchJson<RuntimeApiStatus>("/v0/runtime/status");
        healthStatus = "healthy";
      } catch {
        healthStatus = "unavailable";
      }
    }, 2000);
  }

  function stopHealthPolling() {
    if (healthTimer !== null) {
      clearInterval(healthTimer);
      healthTimer = null;
    }
    healthStatus = "starting";
  }

  // ── Log pane ──────────────────────────────────────────────────────────────
  function connectLogs() {
    disconnectLogs();
    if (!projectName) return;
    logLines = [];
    logVisible = true;
    logAutoScroll = true;
    logEventSource = new EventSource(`/api/projects/${encodeURIComponent(projectName)}/logs`);
    logEventSource.onmessage = (e: MessageEvent<string>) => {
      logLines =
        logLines.length >= 1000 ? [...logLines.slice(-999), e.data] : [...logLines, e.data];
      const el = logEl;
      if (logAutoScroll && el)
        setTimeout(() => {
          el.scrollTop = el.scrollHeight;
        }, 0);
    };
    logEventSource.onerror = () => {
      /* browser retries */
    };
  }

  function disconnectLogs() {
    if (logEventSource) {
      logEventSource.close();
      logEventSource = null;
    }
  }

  function handleLogScroll(e: Event): void {
    const el = e.currentTarget as HTMLDivElement;
    logAutoScroll = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  }

  // ── Commission health panel ───────────────────────────────────────────────
  function startCommissionHealthPolling() {
    stopCommissionHealthPolling();
    void refreshCommissionHealth();
    commissionHealthTimer = setInterval(() => void refreshCommissionHealth(), 2500);
  }

  function stopCommissionHealthPolling() {
    if (commissionHealthTimer !== null) {
      clearInterval(commissionHealthTimer);
      commissionHealthTimer = null;
    }
    commissionRuntimeStatus = null;
    commissionProviderHealth = [];
  }

  async function refreshCommissionHealth() {
    if (!projectName) return;
    let status: RuntimeStatus;
    try {
      status = await fetchJson<RuntimeStatus>("/api/status");
    } catch (err) {
      commissionHealthError = `Control API unavailable: ${err instanceof Error ? err.message : String(err)}`;
      return;
    }
    if (!status.running) {
      commissionHealthError = "Runtime is stopped.";
      commissionRuntimeStatus = null;
      commissionProviderHealth = [];
      return;
    }
    const rp = typeof status.active_project === "string" ? status.active_project : "";
    if (rp !== projectName) {
      commissionHealthError = `Runtime is running for "${rp}". Launch for "${projectName}" is blocked.`;
      return;
    }
    commissionHealthError = "";
    const [rtResult, phResult] = await Promise.allSettled([
      fetchJson<RuntimeApiStatus>("/v0/runtime/status"),
      fetchJson<{ providers: ProviderHealth[] }>("/v0/providers/health"),
    ]);
    commissionRuntimeStatus = rtResult.status === "fulfilled" ? rtResult.value : null;
    commissionProviderHealth =
      phResult.status === "fulfilled" ? (phResult.value?.providers ?? []) : [];
  }

  function stateClass(state: unknown): string {
    const u = String(state || "").toUpperCase();
    if (u === "AVAILABLE" || u === "RUNNING") return "ok";
    if (u === "UNAVAILABLE" || u === "STALE") return "unavailable";
    if (u === "FAULT") return "fault";
    return "unknown";
  }

  // ── Preflight ──────────────────────────────────────────────────────────────
  async function runPreflight() {
    if (!projectName) return;
    preflightRunning = true;
    preflightResults = null;
    actionError = "";
    try {
      const data = await fetchJson<PreflightResult>(
        `/api/projects/${encodeURIComponent(projectName)}/preflight`,
        {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      preflightResults = data;
    } catch (err) {
      actionError = `Preflight error: ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      preflightRunning = false;
    }
  }

  // ── Launch ─────────────────────────────────────────────────────────────────
  function deriveLaunchBlockReason(
    status: RuntimeStatus | null | undefined,
    target: string,
  ): string {
    if (!status || typeof status !== "object") return "";
    const r = Boolean(status.running);
    const rp =
      typeof status.active_project === "string" && status.active_project !== ""
        ? status.active_project
        : null;
    if (!r || !rp) return "";
    if (rp === target)
      return `Launch blocked: runtime is already active for "${target}". Use Stop or Restart.`;
    return `Launch blocked: runtime is active for "${rp}". Stop that runtime before launching "${target}".`;
  }

  async function doLaunch() {
    if (!projectName) return;
    launchRunning = true;
    actionError = "";
    try {
      let status: RuntimeStatus | undefined;
      try {
        status = await fetchJson<RuntimeStatus>("/api/status");
      } catch {
        /* non-fatal pre-check */
      }
      if (status) {
        const block = deriveLaunchBlockReason(status, projectName);
        if (block) {
          actionError = block;
          launchRunning = false;
          return;
        }
      }
      await fetchJson(`/api/projects/${encodeURIComponent(projectName)}/launch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      startHealthPolling();
      connectLogs();
      startCommissionHealthPolling();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      actionError = msg.toLowerCase().includes("already running")
        ? "Launch blocked: runtime is already active. Stop current runtime before launching this project."
        : `Launch failed: ${msg}`;
    } finally {
      launchRunning = false;
    }
  }

  // ── Stop ───────────────────────────────────────────────────────────────────
  async function doStop() {
    if (!projectName) return;
    stopRunning = true;
    actionError = "";
    try {
      await fetchJson(`/api/projects/${encodeURIComponent(projectName)}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch {
      /* best-effort */
    } finally {
      stopHealthPolling();
      disconnectLogs();
      stopCommissionHealthPolling();
      preflightResults = null;
      stopRunning = false;
    }
  }

  // ── Restart ────────────────────────────────────────────────────────────────
  async function doRestart() {
    if (!projectName) return;
    restartRunning = true;
    actionError = "";
    try {
      await fetchJson(`/api/projects/${encodeURIComponent(projectName)}/restart`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      stopHealthPolling();
      disconnectLogs();
      startHealthPolling();
      connectLogs();
    } catch (err) {
      actionError = `Restart failed: ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      restartRunning = false;
    }
  }

  // ── Export ────────────────────────────────────────────────────────────────
  async function doExport() {
    if (!projectName) return;
    exportRunning = true;
    exportFeedback = "";
    exportIsError = false;
    try {
      const res = await fetchResponse(`/api/projects/${encodeURIComponent(projectName)}/export`, {
        method: "POST",
      });
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        const message =
          typeof payload.error === "string" && payload.error.trim() !== ""
            ? payload.error
            : `Export failed (HTTP ${res.status})`;
        throw new ApiResponseError(message, res.status, payload);
      }
      const blob = await res.blob();
      const fallback = `${projectName}.anpkg`;
      const filename = filenameFromContentDisposition(
        res.headers.get("Content-Disposition"),
        fallback,
      );
      downloadBlob(blob, filename);
      exportFeedback = `Exported ${filename}`;
    } catch (err) {
      exportFeedback = `Export failed: ${err instanceof Error ? err.message : String(err)}`;
      exportIsError = true;
    } finally {
      exportRunning = false;
    }
  }
</script>

<section id="workspace-commission" class="workspace visible">
  <div class="workspace-header">
    <h2>Commission</h2>
    <p>Preflight, launch, monitor logs, and supervise runtime bring-up.</p>
  </div>

  {#if runningForOther}
    <div class="workspace-advisory">
      Runtime is currently running for "{runningProject}". Launch for this project is hard-blocked
      until stop.
    </div>
  {/if}

  <!-- Launch panel -->
  <div id="launch-panel" class="launch-panel">
    {#if !commissionRunningForCurrent}
      <!-- Idle: preflight + launch -->
      <div class="launch-idle-bar">
        <button
          class="btn-secondary"
          type="button"
          disabled={preflightRunning}
          onclick={runPreflight}
        >
          {preflightRunning ? "Checking…" : "Preflight Check"}
        </button>
        <button
          class="btn-primary"
          type="button"
          disabled={launchRunning ||
            runningForOther ||
            (preflightResults !== null && !preflightResults.ok)}
          onclick={doLaunch}
        >
          {launchRunning ? "Launching…" : "Launch →"}
        </button>
        {#if preflightResults !== null}
          <span
            class="launch-summary"
            style="color: {preflightResults.ok ? 'var(--success)' : 'var(--danger)'}"
          >
            {preflightResults.ok ? "✓ All checks passed" : "✗ Checks failed"}
          </span>
        {/if}
      </div>

      {#if preflightResults?.checks?.length}
        <div class="preflight-results">
          {#each preflightResults.checks as c (c.name)}
            {@const cls = c.ok === true ? "check-ok" : c.ok === false ? "check-fail" : "check-skip"}
            {@const icon = c.ok === true ? "✓" : c.ok === false ? "✗" : "–"}
            <div class="check-item {cls}">
              <span class="check-icon">{icon}</span>
              <span class="check-name">{c.name}</span>
              {#if c.error || c.hint || c.note}
                <span class="check-extra">
                  {#if c.error}<span class="check-detail">{c.error}</span>{/if}
                  {#if c.hint}<span class="check-hint">{c.hint}</span>{/if}
                  {#if c.note}<span class="check-detail">{c.note}</span>{/if}
                </span>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    {:else}
      <!-- Running state -->
      <div class="launch-running-bar">
        <span class="running-label">
          Running — {projectName} on port {system?.topology?.runtime?.http_port ?? "?"}
        </span>
        <span class="health-badge {healthStatus}">
          {healthStatus === "healthy"
            ? "Healthy"
            : healthStatus === "unavailable"
              ? "Unavailable"
              : "Starting…"}
        </span>
        <button class="btn-secondary btn-sm" type="button" disabled={stopRunning} onclick={doStop}>
          {stopRunning ? "Stopping…" : "Stop"}
        </button>
        <button
          class="btn-secondary btn-sm"
          type="button"
          disabled={restartRunning}
          title="Restarts with last saved config. Unsaved edits will not be applied."
          onclick={doRestart}
        >
          {restartRunning ? "Restarting…" : "Restart"}
        </button>
      </div>

      {#if healthStatus === "healthy"}
        <div style="margin-top:8px;font-size:12px;">
          {#if operatorUiLink()}
            <a href={operatorUiLink()} target="_blank" rel="noopener noreferrer"
              >Open in Operator UI →</a
            >
            <span class="launch-summary" style="margin-left:8px;"
              >Operator UI base URL is configurable.</span
            >
          {:else}
            <span class="launch-summary">Operator UI link is unavailable.</span>
          {/if}
        </div>
      {/if}
    {/if}

    {#if actionError}
      <div class="launch-summary" style="color:var(--danger);margin-top:6px;">{actionError}</div>
    {/if}
  </div>

  <!-- Log pane -->
  {#if logVisible}
    <div id="log-pane" class="log-pane visible">
      <div class="log-header">
        <span class="log-title">Runtime Log</span>
        <button
          type="button"
          class="btn-secondary btn-sm"
          onclick={() => {
            logLines = [];
          }}>Clear</button
        >
      </div>
      <div class="log-content" bind:this={logEl} onscroll={handleLogScroll}>
        {#each logLines as line, i (i)}
          <div class="log-line">{line}</div>
        {/each}
      </div>
    </div>
  {/if}

  <!-- Commission health panel -->
  <div class="commission-health-panel">
    <h3>Runtime Health</h3>
    {#if commissionHealthError}
      <p class="placeholder">{commissionHealthError}</p>
    {:else if commissionRuntimeStatus}
      <div id="commission-runtime-card" class="commission-runtime-card">
        <div class="runtime-status-row">
          <span>Mode: <strong>{commissionRuntimeStatus.mode ?? "UNKNOWN"}</strong></span>
          <span
            >Uptime: {Number.isFinite(Number(commissionRuntimeStatus.uptime_seconds))
              ? `${commissionRuntimeStatus.uptime_seconds}s`
              : "--"}</span
          >
          <span
            >Poll: {Number.isFinite(Number(commissionRuntimeStatus.polling_interval_ms))
              ? `${commissionRuntimeStatus.polling_interval_ms} ms`
              : "--"}</span
          >
          <span
            >Devices: {Number.isFinite(Number(commissionRuntimeStatus.device_count))
              ? String(commissionRuntimeStatus.device_count)
              : "--"}</span
          >
        </div>
      </div>
    {:else}
      <p class="placeholder">Runtime status unavailable.</p>
    {/if}

    <h4>Provider Health</h4>
    <ul id="commission-provider-health" class="commission-provider-health">
      {#if commissionProviderHealth.length === 0}
        <li class="placeholder">No provider health data.</li>
      {:else}
        {#each commissionProviderHealth as prov, i (i)}
          {@const state = typeof prov?.state === "string" ? prov.state : "UNKNOWN"}
          {@const lifecycle =
            typeof prov?.lifecycle_state === "string" ? prov.lifecycle_state : "--"}
          {@const lastSeenAgo = Number(prov?.last_seen_ago_ms)}
          <li class="commission-provider-row">
            <div class="provider-title">
              <span>{prov?.provider_id ?? "unknown"}</span>
              <span class="badge {stateClass(state)}">{state}</span>
            </div>
            <div class="provider-meta">
              lifecycle: {lifecycle} • last seen: {Number.isFinite(lastSeenAgo)
                ? `${lastSeenAgo} ms`
                : "--"}
            </div>
          </li>
        {/each}
      {/if}
    </ul>
  </div>

  <!-- Export package -->
  <div class="commission-export">
    <button
      type="button"
      class="btn-secondary"
      id="btn-export-package"
      disabled={exportRunning}
      onclick={doExport}
    >
      {exportRunning ? "Exporting…" : "Export .anpkg"}
    </button>
    {#if exportFeedback}
      <span
        id="export-package-feedback"
        class="launch-summary"
        style="color: {exportIsError ? 'var(--danger)' : 'var(--success)'}"
      >
        {exportFeedback}
      </span>
    {/if}
  </div>
</section>
