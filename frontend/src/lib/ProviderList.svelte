<script lang="ts">
  import type {
    ProviderCatalog,
    ProviderCatalogEntry,
    ProviderPaths,
    ProviderRuntimeEntry,
    SystemConfig,
    UnknownRecord,
  } from "./contracts";

  /**
   * ProviderList.svelte — provider list with add/remove/kind-switch.
   * Mutates system.topology.runtime.providers, system.topology.providers, system.paths.providers.
   */
  type ProviderDevice = UnknownRecord & {
    id: string;
    type: string;
    address?: string;
  };

  type ProviderConfig = UnknownRecord & {
    kind?: string;
    startup_policy?: string;
    simulation_mode?: string;
    tick_rate_hz?: number;
    provider_name?: string;
    require_live_session?: boolean;
    query_delay_us?: number;
    timeout_ms?: number;
    retry_count?: number;
    discovery?: {
      mode?: string;
      addresses?: string[];
    };
    devices?: ProviderDevice[];
  };

  let {
    system,
    catalog,
    onChanged,
  }: {
    system: SystemConfig;
    catalog: ProviderCatalog | null;
    onChanged: () => void;
  } = $props();

  const SUPPORTED_KINDS = ["sim", "bread", "ezo"];
  const HEX_RE = /^0x[0-9a-fA-F]{2}$/;

  const SIM_DEVICE_TYPES = [
    {
      type: "tempctl",
      display: "Temperature Controller",
      fields: [{ key: "initial_temp", label: "Initial temp (°C)", default: 25.0 }],
    },
    {
      type: "motorctl",
      display: "Motor Controller",
      fields: [{ key: "max_speed", label: "Max speed (RPM)", default: 3000.0 }],
    },
    { type: "relayio", display: "Relay I/O", fields: [] },
    { type: "analogsensor", display: "Analog Sensor", fields: [] },
  ];

  const BREAD_DEVICE_TYPES = [
    { type: "rlht", display: "RLHT Heater" },
    { type: "dcmt", display: "DCMT Motor" },
  ];

  const EZO_DEVICE_TYPES = [
    { type: "ph", display: "pH Sensor" },
    { type: "do", display: "Dissolved Oxygen" },
    { type: "ec", display: "Conductivity" },
    { type: "orp", display: "ORP" },
    { type: "rtd", display: "Temperature (RTD)" },
    { type: "hum", display: "Humidity" },
  ];

  const kindMap = $derived(
    Object.fromEntries((catalog?.providers ?? []).map((p) => [p.kind, p])) as Record<
      string,
      ProviderCatalogEntry
    >,
  );
  const providers = $derived(
    (system?.topology?.runtime?.providers ?? []) as ProviderRuntimeEntry[],
  );

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

  function defaultTopology(kind: string): ProviderConfig {
    switch (kind) {
      case "sim":
        return {
          kind,
          startup_policy: "degraded",
          simulation_mode: "non_interacting",
          tick_rate_hz: 10.0,
          devices: [],
        };
      case "bread":
        return {
          kind,
          provider_name: "",
          require_live_session: false,
          query_delay_us: 10000,
          timeout_ms: 100,
          retry_count: 2,
          discovery: { mode: "manual", addresses: [] },
          devices: [],
        };
      case "ezo":
        return {
          kind,
          provider_name: "",
          query_delay_us: 300000,
          timeout_ms: 300,
          retry_count: 2,
          devices: [],
        };
      default:
        return { kind };
    }
  }

  function defaultPaths(kind: string): ProviderPaths {
    return kind === "bread" || kind === "ezo"
      ? { executable: "", bus_path: "" }
      : { executable: "" };
  }

  function addProvider() {
    const kind = "sim";
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
    system.topology.providers[id] = defaultTopology(kind);
    system.paths.providers = system.paths.providers ?? {};
    system.paths.providers[id] = defaultPaths(kind);
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
    system.topology.providers[id] = defaultTopology(newKind);
    system.paths.providers = system.paths.providers ?? {};
    system.paths.providers[id] = defaultPaths(newKind);
    onChanged();
  }

  function syncBreadAddresses(cfg: ProviderConfig): void {
    cfg.discovery = cfg.discovery ?? { mode: "manual", addresses: [] };
    cfg.discovery.addresses = (cfg.devices ?? [])
      .map((d) => d.address)
      .filter((addr): addr is string => typeof addr === "string" && addr !== "");
  }

  function nextDeviceId(devices: ProviderDevice[], prefix: string): string {
    const nums = devices
      .filter((d) => d.id.startsWith(prefix))
      .map((d) => parseInt(d.id.slice(prefix.length), 10))
      .filter((n) => !isNaN(n));
    return `${prefix}${nums.length ? Math.max(...nums) + 1 : 0}`;
  }

  // Bus path note helper
  function busNoteClass(busPath: string | undefined): string {
    if (!busPath) return "";
    return busPath.startsWith("mock://") ? "bus-note note-success" : "bus-note note-warning";
  }
  function busNoteText(busPath: string | undefined): string {
    if (!busPath) return "";
    return busPath.startsWith("mock://")
      ? "Mock bus mode — no hardware required."
      : "Live hardware path — requires the bus to be connected.";
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

  <div class="provider-list">
    {#each providers as prov (prov.id)}
      {@const id = prov.id}
      {@const isSupported = SUPPORTED_KINDS.includes(prov.kind)}
      {@const cfg = (system?.topology?.providers?.[id] ?? {}) as ProviderConfig}
      {@const provPaths = (system?.paths?.providers?.[id] ?? {}) as ProviderPaths}

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
            disabled={!isSupported}
            onchange={(e: Event) => changeKind(prov, inputTarget(e).value)}
          >
            {#if !isSupported}
              <option value={prov.kind}>Unsupported ({prov.kind})</option>
            {/if}
            {#each SUPPORTED_KINDS as kind (kind)}
              <option value={kind}>{kindMap[kind]?.display_name ?? kind}</option>
            {/each}
          </select>

          <button type="button" class="btn-remove-provider" onclick={() => removeProvider(id)}
            >✕ Remove</button
          >
        </div>

        {#if !isSupported}
          <div class="bus-note note-warning">
            Provider kind "{prov.kind}" is not supported in Composer contract v1. Remove this
            provider or migrate it manually before saving.
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

        <!-- Bus path (bread/ezo only) -->
        {#if prov.kind === "bread" || prov.kind === "ezo"}
          <div class="form-group bus-path-group">
            <label>Bus path</label>
            <input
              type="text"
              spellcheck="false"
              style="font-family:monospace"
              placeholder="/dev/i2c-1 or mock://name"
              value={provPaths.bus_path ?? ""}
              oninput={(e: Event) => {
                system.paths.providers = system.paths.providers ?? {};
                system.paths.providers[id] = system.paths.providers[id] ?? {};
                system.paths.providers[id].bus_path = inputTarget(e).value;
                onChanged();
              }}
            />
            {#if provPaths.bus_path}
              <div class={busNoteClass(provPaths.bus_path)}>{busNoteText(provPaths.bus_path)}</div>
            {/if}
          </div>
        {/if}

        <!-- Typed configuration (collapsible) -->
        <details class="provider-configure" open>
          <summary>Configure</summary>
          <div class="provider-typed-form">
            {#if prov.kind === "sim"}
              <!-- sim startup_policy -->
              <div class="form-group">
                <label>Startup policy</label>
                <select
                  value={cfg.startup_policy ?? "degraded"}
                  onchange={(e: Event) => {
                    cfg.startup_policy = inputTarget(e).value;
                    onChanged();
                  }}
                >
                  <option value="strict">strict</option>
                  <option value="degraded">degraded</option>
                </select>
              </div>
              {#if (cfg.simulation_mode ?? "non_interacting") === "sim"}
                <div
                  class="note-warning"
                  style="font-size:12px;padding:6px 10px;border-radius:4px;margin-bottom:10px;"
                >
                  ⚠ mode=sim requires manual physics_config_path — not editable in this version.
                </div>
              {:else}
                <div class="form-group">
                  <label>Simulation mode</label>
                  <select
                    value={cfg.simulation_mode ?? "non_interacting"}
                    onchange={(e: Event) => {
                      cfg.simulation_mode = inputTarget(e).value;
                      onChanged();
                    }}
                  >
                    <option value="inert">inert</option>
                    <option value="non_interacting">non_interacting</option>
                  </select>
                </div>
                {#if (cfg.simulation_mode ?? "non_interacting") !== "inert"}
                  <div class="form-group">
                    <label>Tick rate (Hz)</label>
                    <input
                      type="number"
                      min="1"
                      max="100"
                      value={cfg.tick_rate_hz ?? 10.0}
                      onchange={(e: Event) => {
                        const n = Number(inputTarget(e).value);
                        if (!isNaN(n)) {
                          cfg.tick_rate_hz = n;
                          onChanged();
                        }
                      }}
                    />
                  </div>
                {/if}
              {/if}

              <!-- sim devices -->
              <div class="device-list-section">
                <h4>Devices</h4>
                <div class="device-list">
                  {#each cfg.devices ?? [] as dev, di (di)}
                    <div class="device-row">
                      <input
                        type="text"
                        class="device-id-input"
                        spellcheck="false"
                        value={dev.id}
                        onblur={(e: Event) => {
                          const v = inputTarget(e).value.trim();
                          if (!v) {
                            inputTarget(e).value = dev.id;
                            return;
                          }
                          if ((cfg.devices ?? []).some((d, i) => d.id === v && i !== di)) {
                            inputTarget(e).value = dev.id;
                            alert(`Device ID "${v}" is already in use.`);
                            return;
                          }
                          dev.id = v;
                          onChanged();
                        }}
                      />
                      <select
                        class="device-type-select"
                        value={dev.type}
                        onchange={(e: Event) => {
                          const old = SIM_DEVICE_TYPES.find((d) => d.type === dev.type);
                          if (old) old.fields.forEach((f) => delete dev[f.key]);
                          dev.type = inputTarget(e).value;
                          const neo = SIM_DEVICE_TYPES.find((d) => d.type === dev.type);
                          if (neo)
                            neo.fields.forEach((f) => {
                              dev[f.key] = f.default;
                            });
                          onChanged();
                        }}
                      >
                        {#each SIM_DEVICE_TYPES as dt (dt.type)}
                          <option value={dt.type}>{dt.display}</option>
                        {/each}
                      </select>
                      <button
                        type="button"
                        class="btn-remove-device"
                        onclick={() => {
                          cfg.devices = cfg.devices ?? [];
                          cfg.devices.splice(di, 1);
                          onChanged();
                        }}>✕</button
                      >
                      {#each SIM_DEVICE_TYPES.find((d) => d.type === dev.type)?.fields ?? [] as f (f.key)}
                        <label class="inline-label">
                          {f.label}:
                          <input
                            type="number"
                            value={dev[f.key] ?? f.default}
                            onchange={(e: Event) => {
                              const n = Number(inputTarget(e).value);
                              if (!isNaN(n)) {
                                dev[f.key] = n;
                                onChanged();
                              }
                            }}
                          />
                        </label>
                      {/each}
                    </div>
                  {/each}
                  <div class="chaos-badge muted">
                    ℹ chaos_control — Fault injection device — always included by the provider, not
                    configurable here.
                  </div>
                </div>
                <button
                  type="button"
                  class="btn-secondary btn-sm"
                  onclick={() => {
                    cfg.devices = cfg.devices ?? [];
                    const newId = nextDeviceId(cfg.devices, "tempctl");
                    cfg.devices.push({ id: newId, type: "tempctl", initial_temp: 25.0 });
                    onChanged();
                  }}>+ Add Device</button
                >
              </div>
            {:else if prov.kind === "bread"}
              <div class="form-group">
                <label>Provider name (optional)</label>
                <input
                  type="text"
                  spellcheck="false"
                  value={cfg.provider_name ?? ""}
                  oninput={(e: Event) => {
                    cfg.provider_name = inputTarget(e).value;
                    onChanged();
                  }}
                />
              </div>
              <div class="form-group form-group-inline">
                <label>
                  <input
                    type="checkbox"
                    checked={cfg.require_live_session ?? false}
                    onchange={(e: Event) => {
                      cfg.require_live_session = inputTarget(e).checked;
                      onChanged();
                    }}
                  />
                  Require live session
                </label>
              </div>
              <div class="form-group">
                <label>Query delay (µs)</label>
                <input
                  type="number"
                  min="0"
                  max="1000000"
                  value={cfg.query_delay_us ?? 10000}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      cfg.query_delay_us = n;
                      onChanged();
                    }
                  }}
                />
              </div>
              <div class="form-group">
                <label>Timeout (ms)</label>
                <input
                  type="number"
                  min="1"
                  max="60000"
                  value={cfg.timeout_ms ?? 100}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      cfg.timeout_ms = n;
                      onChanged();
                    }
                  }}
                />
              </div>
              <div class="form-group">
                <label>Retry count</label>
                <input
                  type="number"
                  min="0"
                  max="20"
                  value={cfg.retry_count ?? 2}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      cfg.retry_count = n;
                      onChanged();
                    }
                  }}
                />
              </div>
              <div class="device-list-section">
                <h4>Devices</h4>
                <div class="device-list">
                  {#each cfg.devices ?? [] as dev, di (di)}
                    {@const addrId = `bread-addr-${id}-${di}`}
                    <div class="device-row">
                      <input
                        type="text"
                        class="device-id-input"
                        spellcheck="false"
                        value={dev.id}
                        onblur={(e: Event) => {
                          const v = inputTarget(e).value.trim();
                          if (!v) {
                            inputTarget(e).value = dev.id;
                            return;
                          }
                          if ((cfg.devices ?? []).some((d, i) => d.id === v && i !== di)) {
                            inputTarget(e).value = dev.id;
                            alert(`Device ID "${v}" is already in use.`);
                            return;
                          }
                          dev.id = v;
                          onChanged();
                        }}
                      />
                      <select
                        class="device-type-select"
                        value={dev.type}
                        onchange={(e: Event) => {
                          dev.type = inputTarget(e).value;
                          onChanged();
                        }}
                      >
                        {#each BREAD_DEVICE_TYPES as dt (dt.type)}
                          <option value={dt.type}>{dt.display}</option>
                        {/each}
                      </select>
                      <input
                        id={addrId}
                        type="text"
                        class="device-addr-input"
                        spellcheck="false"
                        placeholder="0x0A"
                        value={dev.address ?? ""}
                        onblur={(e: Event) => {
                          const v = inputTarget(e).value.trim();
                          if (!HEX_RE.test(v)) {
                            inputTarget(e).classList.add("input-error");
                          } else {
                            inputTarget(e).classList.remove("input-error");
                            dev.address = v;
                            syncBreadAddresses(cfg);
                            onChanged();
                          }
                        }}
                      />
                      <button
                        type="button"
                        class="btn-remove-device"
                        onclick={() => {
                          cfg.devices = cfg.devices ?? [];
                          cfg.devices.splice(di, 1);
                          syncBreadAddresses(cfg);
                          onChanged();
                        }}>✕</button
                      >
                    </div>
                  {/each}
                </div>
                <button
                  type="button"
                  class="btn-secondary btn-sm"
                  onclick={() => {
                    cfg.devices = cfg.devices ?? [];
                    cfg.discovery = cfg.discovery ?? { mode: "manual", addresses: [] };
                    const newId = nextDeviceId(cfg.devices, "rlht");
                    cfg.devices.push({ id: newId, type: "rlht", address: "0x0A" });
                    syncBreadAddresses(cfg);
                    onChanged();
                  }}>+ Add Device</button
                >
              </div>
            {:else if prov.kind === "ezo"}
              <div class="form-group">
                <label>Provider name (optional)</label>
                <input
                  type="text"
                  spellcheck="false"
                  value={cfg.provider_name ?? ""}
                  oninput={(e: Event) => {
                    cfg.provider_name = inputTarget(e).value;
                    onChanged();
                  }}
                />
              </div>
              <div class="form-group">
                <label>Query delay (µs)</label>
                <input
                  type="number"
                  min="0"
                  max="2000000"
                  value={cfg.query_delay_us ?? 300000}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      cfg.query_delay_us = n;
                      onChanged();
                    }
                  }}
                />
              </div>
              <div class="form-group">
                <label>Timeout (ms)</label>
                <input
                  type="number"
                  min="1"
                  max="60000"
                  value={cfg.timeout_ms ?? 300}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      cfg.timeout_ms = n;
                      onChanged();
                    }
                  }}
                />
              </div>
              <div class="form-group">
                <label>Retry count</label>
                <input
                  type="number"
                  min="0"
                  max="20"
                  value={cfg.retry_count ?? 2}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      cfg.retry_count = n;
                      onChanged();
                    }
                  }}
                />
              </div>
              <div class="device-list-section">
                <h4>Devices</h4>
                <div class="device-list">
                  {#each cfg.devices ?? [] as dev, di (di)}
                    <div class="device-row">
                      <input
                        type="text"
                        class="device-id-input"
                        spellcheck="false"
                        value={dev.id}
                        onblur={(e: Event) => {
                          const v = inputTarget(e).value.trim();
                          if (!v) {
                            inputTarget(e).value = dev.id;
                            return;
                          }
                          if ((cfg.devices ?? []).some((d, i) => d.id === v && i !== di)) {
                            inputTarget(e).value = dev.id;
                            alert(`Device ID "${v}" is already in use.`);
                            return;
                          }
                          dev.id = v;
                          onChanged();
                        }}
                      />
                      <select
                        class="device-type-select"
                        value={dev.type}
                        onchange={(e: Event) => {
                          dev.type = inputTarget(e).value;
                          onChanged();
                        }}
                      >
                        {#each EZO_DEVICE_TYPES as dt (dt.type)}
                          <option value={dt.type}>{dt.display}</option>
                        {/each}
                      </select>
                      <input
                        type="text"
                        class="device-addr-input"
                        spellcheck="false"
                        placeholder="0x63"
                        value={dev.address ?? ""}
                        onblur={(e: Event) => {
                          const v = inputTarget(e).value.trim();
                          if (!HEX_RE.test(v)) {
                            inputTarget(e).classList.add("input-error");
                          } else {
                            inputTarget(e).classList.remove("input-error");
                            dev.address = v;
                            onChanged();
                          }
                        }}
                      />
                      <button
                        type="button"
                        class="btn-remove-device"
                        onclick={() => {
                          cfg.devices = cfg.devices ?? [];
                          cfg.devices.splice(di, 1);
                          onChanged();
                        }}>✕</button
                      >
                    </div>
                  {/each}
                </div>
                <button
                  type="button"
                  class="btn-secondary btn-sm"
                  onclick={() => {
                    cfg.devices = cfg.devices ?? [];
                    const newId = nextDeviceId(cfg.devices, "ph");
                    cfg.devices.push({ id: newId, type: "ph", address: "0x63" });
                    onChanged();
                  }}>+ Add Device</button
                >
              </div>
            {:else}
              <p class="muted">Unsupported provider kind in Composer contract v1.</p>
            {/if}
          </div>
        </details>
      </div>
    {/each}
  </div>

  <button type="button" class="btn-secondary" onclick={addProvider}>+ Add Provider</button>
</section>
