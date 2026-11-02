<script lang="ts">
  /**
   * Import a provider's --config-schema envelope from a file (#787): the
   * desktop app's way to do what `anolis-workbench provider import` does. The
   * server runs the CLI's checks and answers with its refusal, shown as is.
   */
  import { fetchJson } from "./api";
  import type { ProviderImportResult } from "./contracts";

  let { onImported }: { onImported: () => void } = $props();

  let replace = $state(false);
  let repo = $state("");
  let busy = $state(false);
  let result = $state("");
  let error = $state("");

  async function importFile(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    busy = true;
    result = "";
    error = "";
    const params: Record<string, string> = { filename: file.name };
    if (replace) params.replace = "1";
    if (repo.trim()) params.repo = repo.trim();
    try {
      const query = new URLSearchParams(params).toString();
      const r = await fetchJson<ProviderImportResult>(`/api/provider-schemas/import?${query}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: file,
      });
      const from = r.repo ? `, deploys from ${r.repo}` : "";
      result = `${r.kind} ${r.version}: ${r.status} (sha256 ${r.sha256.slice(0, 12)}${from})`;
      onImported();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      busy = false;
      // The same file again is a new choice, so a retry with Replace works.
      input.value = "";
    }
  }
</script>

<section class="provider-schema-import" data-testid="provider-schema-import">
  <h3>Provider schemas</h3>
  <p class="field-note">
    A provider kind or version the workbench doesn't package: import its
    <code>--config-schema</code> output. Imports are unverified, and live on this host only.
  </p>
  <label class="inline-label">
    <input type="checkbox" bind:checked={replace} />
    Replace an import of the same version with other content (<code>--replace</code>)
  </label>
  <label class="inline-label">
    Deploys from
    <input
      type="text"
      spellcheck="false"
      style="font-family:monospace"
      placeholder="owner/name (optional)"
      bind:value={repo}
    />
  </label>
  <label class="inline-label">
    Import a provider schema…
    <input type="file" accept=".json,application/json" disabled={busy} onchange={importFile} />
  </label>
  {#if result}
    <p class="bus-note" data-testid="provider-import-result">{result}</p>
  {/if}
  {#if error}
    <p class="bus-note note-warning" role="alert" data-testid="provider-import-error">
      Not imported: {error}
    </p>
  {/if}
</section>
