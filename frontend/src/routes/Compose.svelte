<script lang="ts">
  import RuntimeForm from "../lib/RuntimeForm.svelte";
  import ProviderList from "../lib/ProviderList.svelte";

  let {
    projectName,
    system,
    catalog,
    runtimeStatus,
    onDirty,
    onSaved,
    onSystemChanged,
  }: {
    projectName: string | null;
    system: Record<string, any> | null;
    catalog: Record<string, any> | null;
    runtimeStatus: Record<string, any>;
    onDirty: () => void;
    onSaved: () => void;
    onSystemChanged: (next: Record<string, any> | null) => void;
  } = $props();

  const running = $derived(Boolean(runtimeStatus?.running));
  const runningProject = $derived(
    typeof runtimeStatus?.active_project === "string" ? runtimeStatus.active_project : null,
  );
  const showAdvisory = $derived(running && runningProject === projectName);

  let saving = $state<boolean>(false);
  let saveError = $state<string>("");
  let saveErrors = $state<Array<{ path?: string; message?: string }>>([]);

  function markDirty() {
    onDirty();
  }

  async function handleSave() {
    if (!projectName || !system) return;
    saving = true;
    saveError = "";
    saveErrors = [];
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(projectName)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(system),
      });
      const payload = await res.json().catch(() => ({}));
      if (res.ok) {
        onSaved();
        return;
      }
      saveErrors = Array.isArray(payload?.errors) ? payload.errors : [];
      saveError = payload?.error || "Save failed";
    } catch (err) {
      saveError = err instanceof Error ? err.message : "Save failed";
    } finally {
      saving = false;
    }
  }
</script>

<section id="workspace-compose" class="workspace visible">
  <div class="workspace-header">
    <h2>Compose</h2>
    <p>Define topology and config in <code>system.json</code>.</p>
  </div>

  {#if showAdvisory}
    <div class="workspace-advisory">
      Runtime is currently running from this project. Save edits now; changes take effect only after
      relaunch from Commission.
    </div>
  {/if}

  {#if saveError}
    <div class="error-banner">
      <p>{saveError}</p>
      {#if saveErrors.length > 0}
        <ul>
          {#each saveErrors as e}
            <li><code>{e.path ?? "$"}</code>: {e.message ?? "Validation error"}</li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}

  <div id="compose-form-area">
    {#if system}
      <RuntimeForm {system} onChanged={markDirty} />
      <ProviderList {system} {catalog} onChanged={markDirty} />
    {:else}
      <p class="placeholder">Loading…</p>
    {/if}
  </div>

  <div class="compose-actions">
    <button id="btn-save" type="button" class="btn-primary" disabled={saving} onclick={handleSave}
      >{saving ? "Saving…" : "Save"}</button
    >
  </div>
</section>
