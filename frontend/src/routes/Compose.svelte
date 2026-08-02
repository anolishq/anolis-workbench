<script lang="ts">
  import { ApiResponseError, fetchJson } from "../lib/api";
  import {
    activeVariant,
    AUTOMATION_VARIANT,
    MANUAL_VARIANT,
    projectPathToken,
    variantRelpath,
  } from "../lib/canonical";
  import {
    isImportedDoc,
    type ApiErrorResponse,
    type ProjectDocument,
    type ProviderSchemasResponse,
    type RuntimeStatus,
  } from "../lib/contracts";
  import ProfileForm from "../lib/ProfileForm.svelte";
  import ProfileView from "../lib/ProfileView.svelte";
  import RuntimeForm from "../lib/RuntimeForm.svelte";
  import ProviderList from "../lib/ProviderList.svelte";

  let {
    projectName,
    system,
    providerSchemas,
    runtimeStatus,
    onDirty,
    onSaved,
  }: {
    projectName: string | null;
    system: ProjectDocument | null;
    providerSchemas: ProviderSchemasResponse | null;
    runtimeStatus: RuntimeStatus | null;
    onDirty: () => void;
    onSaved: () => void;
  } = $props();

  const imported = $derived(isImportedDoc(system));
  const editable = $derived(system !== null && !imported ? system : null);

  const variants = $derived(Object.keys(system?.variants ?? {}).sort());
  let selectedVariant = $state<string>("");
  const variant = $derived(
    selectedVariant && system?.variants?.[selectedVariant]
      ? selectedVariant
      : activeVariant(system),
  );

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

  /**
   * Add the separate `automation` variant.
   *
   * install.sh refuses a `manual` variant that boots into automation, so an
   * automated machine needs a second runtime config. Before #255 the composer
   * wrote automation into its ONLY config and every such deploy was rejected
   * at the target.
   */
  function addAutomationVariant(): void {
    const doc = editable;
    if (!doc || doc.variants[AUTOMATION_VARIANT]) return;
    const base = doc.variants[MANUAL_VARIANT] ?? doc.variants[variant] ?? {};
    doc.variants[AUTOMATION_VARIANT] = {
      ...structuredClone($state.snapshot(base)),
      automation: {
        enabled: true,
        behavior_tree: projectPathToken(doc.profile.machine_id, "behaviors/main.xml"),
      },
    };
    doc.profile.runtime_profiles = {
      ...doc.profile.runtime_profiles,
      [AUTOMATION_VARIANT]: variantRelpath(AUTOMATION_VARIANT),
    };
    doc.profile.behaviors = Array.from(
      new Set([...(doc.profile.behaviors ?? []), "behaviors/main.xml"]),
    );
    selectedVariant = AUTOMATION_VARIANT;
    markDirty();
  }

  async function handleSave() {
    if (!projectName || !system) return;
    saving = true;
    saveError = "";
    saveErrors = [];
    try {
      await fetchJson(`/api/projects/${encodeURIComponent(projectName)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(system),
      });
      onSaved();
    } catch (err) {
      if (err instanceof ApiResponseError && err.payload && typeof err.payload === "object") {
        const payload = err.payload as ApiErrorResponse;
        saveErrors = Array.isArray(payload.errors) ? payload.errors : [];
      }
      saveError = err instanceof Error ? err.message : "Save failed";
    } finally {
      saving = false;
    }
  }
</script>

<section id="workspace-compose" class="workspace visible">
  <div class="workspace-header">
    <h2>Compose</h2>
    <p>
      Author the machine profile and its runtime configs. These are the files that get deployed —
      nothing is generated at deploy time.
    </p>
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
          {#each saveErrors as e, i (i)}
            <li><code>{e.path ?? "$"}</code>: {e.message ?? "Validation error"}</li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}

  <div id="compose-form-area">
    {#if system && imported}
      <ProfileView doc={system} />
    {:else if editable}
      <ProfileForm doc={editable} onChanged={markDirty} />

      <div class="variant-bar">
        <label for="variant-select">Runtime variant</label>
        <select
          id="variant-select"
          value={variant}
          onchange={(e: Event) => (selectedVariant = (e.currentTarget as HTMLSelectElement).value)}
        >
          {#each variants as name (name)}
            <option value={name}>{name}</option>
          {/each}
        </select>
        {#if !editable.variants[AUTOMATION_VARIANT]}
          <button type="button" class="btn-add-provider" onclick={addAutomationVariant}
            >+ Automation variant</button
          >
        {/if}
      </div>

      <RuntimeForm doc={editable} {variant} onChanged={markDirty} />
      <ProviderList doc={editable} {variant} {providerSchemas} onChanged={markDirty} />
    {:else}
      <p class="placeholder">Loading…</p>
    {/if}
  </div>

  {#if editable}
    <div class="compose-actions">
      <button id="btn-save" type="button" class="btn-primary" disabled={saving} onclick={handleSave}
        >{saving ? "Saving…" : "Save"}</button
      >
    </div>
  {/if}
</section>

<style>
  .variant-bar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin: 0.5rem 0 1rem;
    flex-wrap: wrap;
  }
</style>
