<script lang="ts">
  /**
   * ConnectionSection.svelte: the runtime's status and this browser's event
   * stream as one grid of facts (wb#748). No mode: Mode and the machine line
   * say that, once each.
   */
  import type { RuntimeApiStatus } from "./contracts";
  import { formatDuration } from "./operate-contracts";

  let {
    status,
    settled,
    refusal,
    health,
    startup,
    stream,
  }: {
    status: RuntimeApiStatus | null;
    /** Whether the first status reply has settled (wb#623). */
    settled: boolean;
    /** The runtime's own reason for refusing the status read, if it gave one. */
    refusal: string;
    health: { label: string; cls: string; reasons: string[] };
    startup: { text: string; ok: boolean; calls: string[] } | null;
    stream: { label: string; cls: string; detail: string };
  } = $props();
</script>

{#snippet streamFact()}
  <div>
    <dt>Event stream</dt>
    <dd>
      <span class="badge {stream.cls}">{stream.label}</span>{#if stream.detail}
        <span class="operate-meta">{stream.detail}</span>{/if}
    </dd>
  </div>
{/snippet}

<div class="operate-section" data-testid="runtime-status">
  <h3>Connection</h3>
  {#if status}
    <div class="conn-head">
      <span class="badge {health.cls}" data-testid="runtime-health">{health.label}</span>
      {#if health.reasons.length}<span class="operate-meta">{health.reasons.join("; ")}.</span>{/if}
    </div>
    <dl class="conn-grid">
      <div>
        <dt>Runtime</dt>
        <dd>{status.version ? `v${status.version}` : "unknown"}</dd>
      </div>
      <div>
        <dt>Devices</dt>
        <dd>{status.device_count}</dd>
      </div>
      <div>
        <dt>Poll</dt>
        <dd>{status.polling_interval_ms} ms</dd>
      </div>
      <div>
        <dt>Up for</dt>
        <dd>{formatDuration(status.uptime_seconds)}</dd>
      </div>
      {@render streamFact()}
    </dl>
    {#if startup}
      <p class={startup.ok ? "field-note" : "field-error"} data-testid="startup-safe-state">
        {startup.text}
      </p>
      {#if startup.calls.length}
        <ul class="operate-meta field-error" data-testid="startup-safe-state-calls">
          {#each startup.calls as call (call)}
            <li>{call}</li>
          {/each}
        </ul>
      {/if}
    {/if}
  {:else if !settled}
    <!-- Until the first status reply settles, a missing status is not a
         fault (wb#623). A failed reply settles too, and falls through. -->
    <span class="badge unknown">--</span>
    <span class="operate-meta">Loading runtime status…</span>
  {:else}
    <span class="badge unavailable">UNAVAILABLE</span>
    {#if refusal}
      <span class="operate-meta field-error" data-testid="runtime-refusal">{refusal}</span>
    {:else}
      <span class="operate-meta">Runtime status unavailable.</span>
    {/if}
  {/if}
  {#if !status}
    <!-- The stream is this browser's, and is shown whatever the status reads. -->
    <dl class="conn-grid">{@render streamFact()}</dl>
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
</style>
