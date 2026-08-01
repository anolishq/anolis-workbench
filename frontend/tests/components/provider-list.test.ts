import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import type {
  ProviderSchemaEnvelope,
  ProviderSchemasResponse,
  ProviderTopologyEntry,
  SystemConfig,
  UnknownRecord,
} from "../../src/lib/contracts";
import ProviderList from "../../src/lib/ProviderList.svelte";
import { createSystemConfig } from "./helpers";
import { reactive } from "./helpers.svelte";

const HERE = dirname(fileURLToPath(import.meta.url));

function loadEnvelope(kind: string): ProviderSchemaEnvelope {
  const path = resolve(HERE, `../../../anolis_workbench/schemas/providers/${kind}.config-schema.json`);
  return JSON.parse(readFileSync(path, "utf-8")) as ProviderSchemaEnvelope;
}

function schemasResponse(): ProviderSchemasResponse {
  return {
    schema_version: 1,
    providers: {
      bread: loadEnvelope("bread"),
      ezo: loadEnvelope("ezo"),
      sim: loadEnvelope("sim"),
    },
  };
}

function systemWithSim(): SystemConfig {
  const system = createSystemConfig("demo");
  system.topology.runtime.providers = [
    {
      id: "sim0",
      kind: "sim",
      timeout_ms: 5000,
      hello_timeout_ms: 3000,
      ready_timeout_ms: 10000,
      restart_policy: { enabled: false },
    },
  ];
  system.topology.providers = {
    sim0: {
      kind: "sim",
      config: {
        provider: { name: "sim0" },
        startup_policy: "degraded",
        devices: [],
        simulation: { mode: "non_interacting", tick_rate_hz: 10 },
      },
    },
  };
  system.paths.providers = { sim0: { executable: "/bin/sim" } };
  return reactive(system);
}

describe("ProviderList.svelte", () => {
  it("degrades gracefully with providerSchemas null", () => {
    render(ProviderList, {
      props: { system: systemWithSim(), providerSchemas: null, onChanged: vi.fn() },
    });

    expect(screen.getByText(/Provider schemas are unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add Provider" })).toBeDisabled();
    // No schema — no schema-driven form, but the row itself renders
    expect(screen.getByDisplayValue("sim0")).toBeInTheDocument();
    expect(screen.queryByText("Configure")).not.toBeInTheDocument();
  });

  it("renders the schema-driven form with kind labels from envelopes", () => {
    render(ProviderList, {
      props: { system: systemWithSim(), providerSchemas: schemasResponse(), onChanged: vi.fn() },
    });

    expect(screen.getByRole("option", { name: "anolis-provider-sim" })).toBeInTheDocument();
    expect(screen.getByText("Configure")).toBeInTheDocument();
    expect(screen.getByText("Startup policy")).toBeInTheDocument();
    expect(screen.getByText("Simulation mode")).toBeInTheDocument();
  });

  it("kind change seeds a native config: defaults + provider.name=id + discovery.mode=manual", async () => {
    const system = systemWithSim();
    const onChanged = vi.fn();
    render(ProviderList, {
      props: { system, providerSchemas: schemasResponse(), onChanged },
    });

    const kindSelect = document.querySelector(".provider-kind-select") as HTMLSelectElement;
    await fireEvent.change(kindSelect, { target: { value: "bread" } });

    const entry = system.topology.providers?.sim0 as ProviderTopologyEntry;
    expect(entry.kind).toBe("bread");
    expect(entry.config.provider).toEqual({ name: "sim0" });
    expect(entry.config.hardware).toEqual({
      query_delay_us: 10000,
      timeout_ms: 100,
      retry_count: 2,
    });
    expect(entry.config.discovery).toEqual({ mode: "manual" });
    expect(entry.config.devices).toEqual([]);
    expect(system.paths.providers?.sim0).toEqual({ executable: "" });
    expect(onChanged).toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.getByText("I2C bus path")).toBeInTheDocument();
    });
  });

  it("add provider seeds a sim entry with schema defaults", async () => {
    const system = reactive(createSystemConfig("demo"));
    const onChanged = vi.fn();
    render(ProviderList, {
      props: { system, providerSchemas: schemasResponse(), onChanged },
    });

    await fireEvent.click(screen.getByRole("button", { name: "+ Add Provider" }));

    const entry = system.topology.providers?.sim0 as ProviderTopologyEntry;
    expect(entry.kind).toBe("sim");
    expect(entry.config.provider).toEqual({ name: "sim0" });
    expect(entry.config.startup_policy).toBe("strict");
    expect((entry.config.simulation as UnknownRecord).mode).toBeUndefined();
    expect(system.topology.runtime.providers).toHaveLength(1);
    expect(onChanged).toHaveBeenCalled();
  });

  it("tolerates a malformed document (topology.providers as array)", () => {
    const system = reactive({
      ...createSystemConfig("demo"),
      topology: {
        runtime: { http_port: 8080, providers: [{ id: "ghost0", kind: "sim" }] },
        providers: [] as unknown as Record<string, ProviderTopologyEntry>,
      },
    }) as SystemConfig;

    render(ProviderList, {
      props: { system, providerSchemas: schemasResponse(), onChanged: vi.fn() },
    });

    expect(screen.getByDisplayValue("ghost0")).toBeInTheDocument();
  });
});
