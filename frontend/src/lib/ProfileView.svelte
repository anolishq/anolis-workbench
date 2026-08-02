<script lang="ts">
  import type { ImportedProjectDoc, UnknownRecord } from "./contracts";

  /**
   * ProfileView.svelte — read-only view of an imported machine-profile
   * project (#226). The workbench carries the profile verbatim; the source
   * repository is the place to edit it.
   */
  let { doc }: { doc: ImportedProjectDoc } = $props();

  const profile = $derived(doc.profile ?? {});
  const warnings = $derived(Array.isArray(doc.warnings) ? doc.warnings : []);

  function asRecord(value: unknown): UnknownRecord {
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as UnknownRecord)
      : {};
  }

  const runtimeProfiles = $derived(Object.entries(asRecord(profile.runtime_profiles)));
  const providers = $derived(Object.entries(asRecord(profile.providers)));
  const behaviors = $derived(
    Array.isArray(profile.behaviors) ? (profile.behaviors as unknown[]).map(String) : [],
  );
  const safety = $derived(asRecord(profile.safety));
  const components = $derived(asRecord(profile.components));
  const componentRuntime = $derived(asRecord(components.runtime));
  const componentProviders = $derived(Object.entries(asRecord(components.providers)));
  const importedFrom = $derived(
    typeof doc.meta?.imported_from === "string" ? (doc.meta.imported_from as string) : null,
  );
</script>

<div class="profile-view">
  <div class="workspace-advisory">
    This project was imported from a machine profile and is carried <strong>verbatim</strong> — the
    workbench never rewrites it. Edit it in its source repository and re-import.
    {#if importedFrom}
      <div><code>{importedFrom}</code></div>
    {/if}
  </div>

  {#if warnings.length > 0}
    <div class="error-banner">
      <p>Import warnings</p>
      <ul>
        {#each warnings as warning, i (i)}
          <li>{warning}</li>
        {/each}
      </ul>
    </div>
  {/if}

  <section class="form-section">
    <h3>Machine</h3>
    <dl class="profile-facts">
      <dt>machine_id</dt>
      <dd><code>{profile.machine_id ?? "—"}</code></dd>
      <dt>Display name</dt>
      <dd>{profile.display_name ?? "—"}</dd>
      {#if safety.estop_topology}
        <dt>E-stop topology</dt>
        <dd><code>{safety.estop_topology}</code></dd>
      {/if}
    </dl>
  </section>

  <section class="form-section">
    <h3>Runtime variants</h3>
    {#if runtimeProfiles.length === 0}
      <p class="muted">No runtime_profiles declared.</p>
    {:else}
      <ul class="profile-list">
        {#each runtimeProfiles as [variant, path] (variant)}
          <li><strong>{variant}</strong> — <code>{path}</code></li>
        {/each}
      </ul>
    {/if}
  </section>

  <section class="form-section">
    <h3>Providers</h3>
    <ul class="profile-list">
      {#each providers as [pid, entry] (pid)}
        {@const record = asRecord(entry)}
        <li>
          <strong>{pid}</strong>
          {#if record.config}
            — <code>{record.config}</code>{/if}
          {#if record.notes}
            <div class="field-note">{record.notes}</div>
          {/if}
        </li>
      {/each}
    </ul>
  </section>

  {#if behaviors.length > 0}
    <section class="form-section">
      <h3>Behaviors</h3>
      <ul class="profile-list">
        {#each behaviors as behavior (behavior)}
          <li><code>{behavior}</code></li>
        {/each}
      </ul>
    </section>
  {/if}

  {#if componentProviders.length > 0 || componentRuntime.version}
    <section class="form-section">
      <h3>Pinned components</h3>
      <ul class="profile-list">
        {#if componentRuntime.version}
          <li>
            <strong>runtime</strong> — <code>{componentRuntime.repo}</code>
            v{componentRuntime.version}
          </li>
        {/if}
        {#each componentProviders as [kind, entry] (kind)}
          {@const record = asRecord(entry)}
          <li><strong>{kind}</strong> — <code>{record.repo}</code> v{record.version}</li>
        {/each}
      </ul>
    </section>
  {/if}
</div>
