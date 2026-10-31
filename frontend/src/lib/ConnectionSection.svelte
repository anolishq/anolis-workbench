<script lang="ts">
  /**
   * ConnectionSection.svelte: Runtime Status and Event Stream as one section
   * (wb#748). A spike: three layouts, picked with ?connection=a|b|c, for the
   * maintainer to choose from on the rig.
   */
  import type { RuntimeApiStatus } from "./contracts";
  import { formatDuration } from "./operate-contracts";

  type Summary = { label: string; cls: string };
  let {
    layout,
    status,
    health,
    startup,
    stream,
  }: {
    layout: "a" | "b" | "c";
    status: RuntimeApiStatus;
    health: Summary & { reasons: string[] };
    startup: { text: string; ok: boolean; calls: string[] } | null;
    stream: Summary & { detail: string };
  } = $props();

  const uptime = $derived(formatDuration(status.uptime_seconds));
  const poll = $derived(`${status.polling_interval_ms} ms`);
  const version = $derived(status.version ? `v${status.version}` : "unknown");
</script>

{#snippet startupCalls()}
  {#if startup?.calls.length}
    <ul class="operate-meta field-error" data-testid="startup-safe-state-calls">
      {#each startup.calls as call (call)}
        <li>{call}</li>
      {/each}
    </ul>
  {/if}
{/snippet}

<div class="operate-section connection-{layout}" data-testid="runtime-status">
  <h3>Connection</h3>

  {#if layout === "a"}
    <!-- A: a facts grid, one label over one value, health first. -->
    <div class="conn-head">
      <span class="badge {health.cls}" data-testid="runtime-health">{health.label}</span>
      {#if health.reasons.length}<span class="operate-meta">{health.reasons.join("; ")}.</span>{/if}
    </div>
    <dl class="conn-grid">
      <div>
        <dt>Runtime</dt>
        <dd>{version}</dd>
      </div>
      <div>
        <dt>Mode</dt>
        <dd>{status.mode}</dd>
      </div>
      <div>
        <dt>Devices</dt>
        <dd>{status.device_count}</dd>
      </div>
      <div>
        <dt>Poll</dt>
        <dd>{poll}</dd>
      </div>
      <div>
        <dt>Up for</dt>
        <dd data-testid="conn-uptime">{uptime}</dd>
      </div>
      <div>
        <dt>Event stream</dt>
        <dd><span class="badge {stream.cls}">{stream.label}</span> {stream.detail}</dd>
      </div>
    </dl>
    {#if startup}
      <p class={startup.ok ? "field-note" : "field-error"} data-testid="startup-safe-state">
        {startup.text}
      </p>
      {@render startupCalls()}
    {/if}
  {:else if layout === "b"}
    <!-- B: one sentence a person would say, then the numbers in a strip. -->
    <p class="conn-sentence">
      <span class="badge {health.cls}" data-testid="runtime-health">{health.label}</span>
      Runtime {version} in <strong>{status.mode}</strong>, up for
      <strong data-testid="conn-uptime">{uptime}</strong>. Event stream
      <span class="badge {stream.cls}">{stream.label}</span>{stream.detail
        ? ` (${stream.detail})`
        : ""}.
    </p>
    {#if health.reasons.length}<p class="field-error">{health.reasons.join("; ")}.</p>{/if}
    <p class="operate-meta conn-strip">
      <span>{status.device_count} devices</span>
      <span>polled every {poll}</span>
      {#if startup}
        <span class={startup.ok ? "field-note" : "field-error"} data-testid="startup-safe-state"
          >{startup.text}</span
        >
      {/if}
    </p>
    {@render startupCalls()}
  {:else}
    <!-- C: two columns, the runtime on the left, this browser's link on the right. -->
    <div class="conn-columns">
      <div>
        <h4>
          Runtime <span class="badge {health.cls}" data-testid="runtime-health">{health.label}</span
          >
        </h4>
        <table class="conn-table">
          <tbody>
            <tr><th scope="row">Version</th><td>{version}</td></tr>
            <tr><th scope="row">Mode</th><td>{status.mode}</td></tr>
            <tr><th scope="row">Up for</th><td data-testid="conn-uptime">{uptime}</td></tr>
            <tr><th scope="row">Devices</th><td>{status.device_count}, polled every {poll}</td></tr>
          </tbody>
        </table>
        {#if health.reasons.length}<p class="field-error">{health.reasons.join("; ")}.</p>{/if}
      </div>
      <div>
        <h4>This browser</h4>
        <p>Event stream <span class="badge {stream.cls}">{stream.label}</span></p>
        {#if stream.detail}<p class="operate-meta">{stream.detail}</p>{/if}
      </div>
    </div>
    {#if startup}
      <p class={startup.ok ? "field-note" : "field-error"} data-testid="startup-safe-state">
        {startup.text}
      </p>
      {@render startupCalls()}
    {/if}
  {/if}
</div>

<style>
  .conn-head {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 8px;
  }
  .conn-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr));
    gap: 8px 16px;
    margin: 0 0 8px;
  }
  .conn-grid dt {
    font-size: 0.8em;
    opacity: 0.7;
  }
  .conn-grid dd {
    margin: 0;
    font-size: 1.1em;
    font-weight: 600;
  }
  .conn-sentence {
    font-size: 1.05em;
    margin: 0 0 6px;
  }
  .conn-strip {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
  }
  .conn-columns {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    gap: 8px 24px;
  }
  .conn-columns h4 {
    margin: 0 0 6px;
  }
  .conn-table th {
    text-align: left;
    font-weight: normal;
    opacity: 0.7;
    padding-right: 12px;
  }
</style>
