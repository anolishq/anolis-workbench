<script lang="ts">
  import type {
    ProviderRuntimeEntry,
    ProviderSchemasResponse,
    SystemConfig,
    UnknownRecord,
  } from "./contracts";
  import SchemaForm from "./schema-form/SchemaForm.svelte";
  import { asObject, defaultsFor, properties, type SchemaNode } from "./schema-form/schema";

  /**
   * ProviderList.svelte — provider list with add/remove/kind-switch (#270).
   * Provider config is rendered schema-driven from the vendored
   * --config-schema envelopes; this component has no per-kind knowledge.
   * Mutates system.topology.runtime.providers, system.topology.providers,
   * system.paths.providers.
   */
  let {
    system,
    providerSchemas,
    onChanged,
  }: {
    system: SystemConfig;
    providerSchemas: ProviderSchemasResponse | null;
    onChanged: () => void;
  } = $props();

  // The set of composable kinds IS the set of vendored envelopes.
  const kinds = $derived(Object.keys(providerSchemas?.providers ?? {}).sort());

  // Display labels come from the provider-owned schema envelopes: the schema
  // title (minus the " configuration" suffix) or the provider binary name.
  const kindLabels = $derived(
    Object.fromEntries(
      Object.entries(providerSchemas?.providers ?? {}).map(([kind, env]) => {
        const title = typeof env.schema?.title === "string" ? (env.schema.title as string) : "";
        const label = title.replace(/ configuration$/i, "") || (env.provider as string) || kind;
        return [kind, label];
      }),
    ) as Record<string, string>,
  );
  const providers = $derived(
    (system?.topology?.runtime?.providers ?? []) as ProviderRuntimeEntry[],
  );

  // A loaded document guarantees {kind, config} per entry (migrate-on-load),
  // but tolerate hand-edited files: ensure every entry has a config object.
  $effect(() => {
    const topo = asObject(system?.topology?.providers);
    if (!topo) return;
    for (const entry of Object.values(topo)) {
      const record = asObject(entry);
      if (record && asObject(record.config) === null) record.config = {};
    }
  });

  // ── helpers ────────────────────────────────────────────────────────────────

  function inputTarget(event: Event): HTMLInputElement {
    return event.currentTarget as HTMLInputElement;
  }

  function genId(kind: string): string {
    const existing = (system.topology.runtime.providers ?? [])
      .filter((p) => p.id.startsWith(kind))
      .map((p) => parseInt(p.id.slice(kind.length), 10))
      .filter((n) => !isNaN(n));
    const next = existing.length ? Math.max(...existing) + 1 : 0;
    return `${kind}${next}`;
  }

  function envelopeSchema(kind: string): SchemaNode | null {
    const schema = providerSchemas?.providers?.[kind]?.schema;
    return asObject(schema);
  }

  /**
   * Seed a fresh native config for a kind: schema defaults/consts, the
   * provider instance name set to the id, and discovery.mode prefilled to
   * "manual" when the schema offers a mode choice (the composer's authoring
   * baseline; scan stays one select away).
   */
  function seedConfig(kind: string, id: string): UnknownRecord {
    const schema = envelopeSchema(kind);
    if (!schema) return {};
    const config = defaultsFor(schema);
    const schemaProps = Object.fromEntries(properties(schema));

    if (schemaProps.provider) {
      const provider = asObject(config.provider) ?? {};
      provider.name = id;
      config.provider = provider;
    }

    const discoverySchema = asObject(schemaProps.discovery);
    if (discoverySchema && properties(discoverySchema).some(([k]) => k === "mode")) {
      const discovery = asObject(config.discovery) ?? {};
      if (discovery.mode === undefined) discovery.mode = "manual";
      config.discovery = discovery;
    }
    return config;
  }

  function addProvider(): void {
    if (kinds.length === 0) return;
    const kind = kinds.includes("sim") ? "sim" : kinds[0];
    const id = genId(kind);
    system.topology.runtime.providers = [
      ...(system.topology.runtime.providers ?? []),
      {
        id,
        kind,
        timeout_ms: 5000,
        hello_timeout_ms: 3000,
        ready_timeout_ms: 10000,
        restart_policy: { enabled: false },
      },
    ];
    system.topology.providers = system.topology.providers ?? {};
    system.topology.providers[id] = { kind, config: seedConfig(kind, id) };
    system.paths.providers = system.paths.providers ?? {};
    system.paths.providers[id] = { executable: "" };
    onChanged();
  }

  function removeProvider(id: string): void {
    const runtimeProviders = system.topology.runtime.providers ?? [];
    system.topology.runtime.providers = runtimeProviders.filter((p) => p.id !== id);
    delete system.topology.providers?.[id];
    if (system.paths.providers) delete system.paths.providers[id];
    onChanged();
  }

  function renameId(oldId: string, newId: string): void {
    if (system.topology.providers?.[oldId] !== undefined) {
      system.topology.providers[newId] = system.topology.providers[oldId];
      delete system.topology.providers[oldId];
    }
    if (system.paths?.providers?.[oldId] !== undefined) {
      system.paths.providers[newId] = system.paths.providers[oldId];
      delete system.paths.providers[oldId];
    }
  }

  function changeKind(provEntry: ProviderRuntimeEntry, newKind: string): void {
    const id = provEntry.id;
    provEntry.kind = newKind;
    system.topology.providers = system.topology.providers ?? {};
    system.topology.providers[id] = { kind: newKind, config: seedConfig(newKind, id) };
    system.paths.providers = system.paths.providers ?? {};
    system.paths.providers[id] = { executable: "" };
    onChanged();
  }

  function formatBackoff(v: unknown): string {
    return Array.isArray(v) ? v.join(", ") : "";
  }
  function parseBackoff(s: string): number[] {
    return String(s)
      .split(",")
      .map((p) => p.trim())
      .filter(Boolean)
      .map(Number)
      .filter((n) => !isNaN(n));
  }
</script>

<section class="form-section providers-section">
  <h3>Providers</h3>

  {#if providerSchemas === null}
    <p class="muted">
      Provider schemas are unavailable — provider configuration cannot be edited right now.
    </p>
  {/if}

  <div class="provider-list">
    {#each providers as prov (prov.id)}
      {@const id = prov.id}
      {@const isKnownKind = kinds.includes(prov.kind)}
      {@const cfgEntry = asObject(system?.topology?.providers?.[id])}
      {@const cfgValue = cfgEntry ? asObject(cfgEntry.config) : null}
      {@const schema = envelopeSchema(prov.kind)}
      {@const provPaths = asObject(system?.paths?.providers?.[id]) ?? {}}

      <div class="provider-row">
        <!-- Header: id, kind, remove -->
        <div class="provider-row-header">
          <input
            type="text"
            class="provider-id-input"
            spellcheck="false"
            title="Provider ID"
            value={id}
            onblur={(e: Event) => {
              const newId = inputTarget(e).value.trim();
              if (!newId || newId === id) {
                inputTarget(e).value = id;
                return;
              }
              if (providers.some((p) => p.id === newId)) {
                inputTarget(e).value = id;
                alert(`Provider ID "${newId}" is already in use.`);
                return;
              }
              renameId(id, newId);
              prov.id = newId;
              onChanged();
            }}
          />

          <select
            class="provider-kind-select"
            value={prov.kind}
            disabled={kinds.length === 0}
            onchange={(e: Event) => changeKind(prov, inputTarget(e).value)}
          >
            {#if !isKnownKind}
              <option value={prov.kind}>Unknown ({prov.kind})</option>
            {/if}
            {#each kinds as kind (kind)}
              <option value={kind}>{kindLabels[kind] ?? kind}</option>
            {/each}
          </select>

          <button type="button" class="btn-remove-provider" onclick={() => removeProvider(id)}
            >✕ Remove</button
          >
        </div>

        {#if !isKnownKind && providerSchemas !== null}
          <div class="bus-note note-warning">
            No config schema is vendored for provider kind "{prov.kind}". Pick a known kind or
            remove this provider before saving.
          </div>
        {/if}

        <!-- Timeouts -->
        <div class="provider-timing">
          <span class="field-group-label">Timeouts</span>
          {#each [["timeout_ms", "timeout"], ["hello_timeout_ms", "hello"], ["ready_timeout_ms", "ready"]] as [key, display] (key)}
            <label class="inline-label">
              <input
                type="number"
                min="100"
                max="120000"
                value={prov[key] ?? ""}
                onchange={(e: Event) => {
                  const n = Number(inputTarget(e).value);
                  if (!isNaN(n)) {
                    prov[key] = n;
                    onChanged();
                  }
                }}
              />
              ms ({display})
            </label>
          {/each}
        </div>

        <!-- Restart policy -->
        <div class="provider-restart">
          <label>
            <input
              type="checkbox"
              checked={prov.restart_policy?.enabled ?? false}
              onchange={(e: Event) => {
                prov.restart_policy = prov.restart_policy ?? {};
                prov.restart_policy.enabled = inputTarget(e).checked;
                if (inputTarget(e).checked) {
                  prov.restart_policy.max_attempts = prov.restart_policy.max_attempts ?? 3;
                  prov.restart_policy.backoff_ms = Array.isArray(prov.restart_policy.backoff_ms)
                    ? prov.restart_policy.backoff_ms
                    : [200, 500, 1000];
                  prov.restart_policy.timeout_ms = prov.restart_policy.timeout_ms ?? 30000;
                }
                onChanged();
              }}
            />
            Enable restart policy
          </label>

          {#if prov.restart_policy?.enabled}
            <div class="restart-fields">
              <label class="inline-label">
                <input
                  type="number"
                  min="0"
                  value={prov.restart_policy.max_attempts ?? 3}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      const rp = prov.restart_policy ?? (prov.restart_policy = {});
                      rp.max_attempts = n;
                      onChanged();
                    }
                  }}
                />
                max attempts
              </label>
              <label class="inline-label">
                <input
                  type="text"
                  spellcheck="false"
                  style="font-family:monospace"
                  placeholder="200, 500, 1000"
                  value={formatBackoff(prov.restart_policy.backoff_ms)}
                  onchange={(e: Event) => {
                    const rp = prov.restart_policy ?? (prov.restart_policy = {});
                    rp.backoff_ms = parseBackoff(inputTarget(e).value);
                    onChanged();
                  }}
                />
                backoff ms
              </label>
              <label class="inline-label">
                <input
                  type="number"
                  min="0"
                  value={prov.restart_policy.timeout_ms ?? 30000}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      const rp = prov.restart_policy ?? (prov.restart_policy = {});
                      rp.timeout_ms = n;
                      onChanged();
                    }
                  }}
                />
                timeout (ms)
              </label>
              <label class="inline-label">
                <input
                  type="number"
                  min="0"
                  value={prov.restart_policy.success_reset_ms ?? ""}
                  onchange={(e: Event) => {
                    const rp = prov.restart_policy ?? (prov.restart_policy = {});
                    const v = inputTarget(e).value.trim();
                    if (v === "") {
                      delete rp.success_reset_ms;
                    } else {
                      const n = Number(v);
                      if (!isNaN(n)) rp.success_reset_ms = n;
                    }
                    onChanged();
                  }}
                />
                success reset (ms)
              </label>
            </div>
          {/if}
        </div>

        <!-- Executable path -->
        <div class="form-group">
          <label>Executable path</label>
          <input
            type="text"
            spellcheck="false"
            style="font-family:monospace"
            value={provPaths.executable ?? ""}
            oninput={(e: Event) => {
              system.paths.providers = system.paths.providers ?? {};
              system.paths.providers[id] = system.paths.providers[id] ?? {};
              system.paths.providers[id].executable = inputTarget(e).value;
              onChanged();
            }}
          />
        </div>

        <!-- Schema-driven provider configuration -->
        {#if schema && cfgValue}
          <details class="provider-configure" open>
            <summary>Configure</summary>
            <div class="provider-typed-form">
              <SchemaForm {schema} value={cfgValue} {onChanged} />
            </div>
          </details>
        {/if}
      </div>
    {/each}
  </div>

  <button type="button" class="btn-add-provider" onclick={addProvider} disabled={kinds.length === 0}
    >+ Add Provider</button
  >
</section>
