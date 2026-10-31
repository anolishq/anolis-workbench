import { render, screen } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import ConnectionSection from "../../src/lib/ConnectionSection.svelte";
import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// wb#748 spike: Runtime Status and Event Stream as one Connection section,
// in one of three layouts picked with ?connection=a|b|c.

const STATUS = {
  status: { code: "OK" },
  version: "0.8.0",
  mode: "MANUAL",
  uptime_seconds: 53720,
  polling_interval_ms: 2500,
  providers: [],
  device_count: 5,
  device_loss_latched: [],
};

afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});

describe("ConnectionSection.svelte (wb#748 spike)", () => {
  it.each(["a", "b", "c"] as const)(
    "layout %s shows every fact, uptime as a duration",
    (layout) => {
      render(ConnectionSection, {
        props: {
          layout,
          status: STATUS,
          health: { label: "OK", cls: "ok", reasons: [] },
          startup: {
            text: "Startup safe state (hooks): every call it issued was accepted by its provider.",
            ok: true,
            calls: [],
          },
          stream: { label: "Quiet", cls: "stale", detail: "no events for 12 s" },
        },
      });
      const section = screen.getByTestId("runtime-status");
      expect(screen.getByRole("heading", { name: "Connection" })).toBeInTheDocument();
      expect(screen.getByTestId("conn-uptime")).toHaveTextContent("14 h 55 min");
      for (const text of ["v0.8.0", "MANUAL", "5", "2500 ms", "Quiet", "no events for 12 s"])
        expect(section).toHaveTextContent(text);
      expect(screen.getByTestId("startup-safe-state")).toHaveTextContent("every call it issued");
      expect(screen.getByTestId("runtime-health")).toHaveTextContent("OK");
    },
  );
});

describe("Operate.svelte with ?connection (wb#748 spike)", () => {
  function renderOperate() {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === "/api/status")
          return jsonResponse(200, { running: true, active_project: "demo" });
        if (path === "/v0/runtime/status")
          return jsonResponse(200, {
            ...STATUS,
            estop: {
              latched: false,
              software_safe_state: "hooks",
              latched_at_epoch_ms: null,
              uncovered_actuating_functions: 0,
            },
          });
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: {
        projectName: "demo",
        system: createProjectDocument("demo"),
        runtimeStatus: { running: true, active_project: "demo" },
      },
    });
  }

  it("shows one Connection section in the chosen layout", async () => {
    window.history.replaceState({}, "", "/?connection=b");
    renderOperate();

    expect(await screen.findByRole("heading", { name: "Connection" })).toBeInTheDocument();
    expect(screen.getByTestId("runtime-status")).toHaveClass("connection-b");
    expect(screen.queryByRole("heading", { name: "Event Stream" })).toBeNull();
  });

  it("is unchanged without the parameter", async () => {
    renderOperate();

    expect(await screen.findByRole("heading", { name: "Runtime Status" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Event Stream" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connection" })).toBeNull();
  });
});
