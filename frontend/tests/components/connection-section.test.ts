import { render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// wb#748: Runtime Status and Event Stream are one Connection section, a grid
// of facts: the health badge, then Runtime, Devices, Poll, Up for and Event
// stream. The machine line is the one place that says the machine's state
// and what to do next, so Mode is shown once, in Mode, and nothing else
// repeats the line.

const RUNNING = { running: true, active_project: "demo" };

function fakeRuntime(opts: { mode?: string; down?: boolean } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      if (path === "/v0/runtime/status") {
        if (opts.down) return jsonResponse(503, { status: { code: "UNAVAILABLE" } });
        return jsonResponse(200, {
          status: { code: "OK" },
          version: "0.8.0",
          mode: opts.mode ?? "MANUAL",
          uptime_seconds: 53720,
          polling_interval_ms: 2500,
          providers: [],
          device_count: 5,
          device_loss_latched: [],
          estop: {
            latched: false,
            software_safe_state: "hooks",
            latched_at_epoch_ms: null,
            uncovered_actuating_functions: 0,
          },
        });
      }
      if (path === "/v0/mode")
        return jsonResponse(200, { status: { code: "OK" }, mode: opts.mode ?? "MANUAL" });
      return jsonResponse(200, {});
    }),
  );
  render(Operate, {
    props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const connection = () => screen.getByTestId("runtime-status");

describe("Operate.svelte — the Connection section (wb#748)", () => {
  it("shows the health badge, then each fact under its label, uptime as a duration", async () => {
    fakeRuntime();

    await waitFor(() => expect(connection()).toHaveTextContent("v0.8.0"));
    expect(within(connection()).getByRole("heading", { level: 3 })).toHaveTextContent("Connection");
    expect(within(connection()).getByTestId("runtime-health")).toHaveTextContent("OK");
    const facts = Object.fromEntries(
      within(connection())
        .getAllByRole("term")
        .map((dt) => [dt.textContent?.trim(), dt.nextElementSibling?.textContent?.trim()]),
    );
    expect(Object.keys(facts)).toEqual(["Runtime", "Devices", "Poll", "Up for", "Event stream"]);
    expect(facts).toMatchObject({
      Runtime: "v0.8.0",
      Devices: "5",
      Poll: "2500 ms",
    });
    expect(facts["Up for"]).toBe("14 h 55 min");
    expect(facts["Event stream"]).toMatch(/^(Connected|Reconnecting|Quiet|Disconnected)/);
    expect(screen.queryByRole("heading", { name: "Runtime Status" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Event Stream" })).toBeNull();
  });

  it("says when the runtime is not answering, in the same section, with the event stream", async () => {
    fakeRuntime({ down: true });

    await waitFor(() => expect(connection()).toHaveTextContent("UNAVAILABLE"));
    expect(within(connection()).getByRole("heading", { level: 3 })).toHaveTextContent("Connection");
    expect(within(connection()).getByText("Event stream")).toBeInTheDocument();
  });
});

describe("Operate.svelte — each thing said once (wb#748)", () => {
  it("shows the mode in Mode only", async () => {
    fakeRuntime({ mode: "MANUAL" });

    await waitFor(() => expect(connection()).toHaveTextContent("v0.8.0"));
    expect(connection()).not.toHaveTextContent(/mode|MANUAL/i);
    const naming = screen
      .queryAllByText(/MANUAL/)
      .filter((el) => !el.closest("select") && !el.closest("[data-testid='mode-section']"));
    expect(naming).toEqual([]);
  });

  it("says what to do in FAULT on the machine line only", async () => {
    fakeRuntime({ mode: "FAULT" });

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent("set MANUAL to recover"),
    );
    expect(screen.queryByTestId("mode-fault-note")).toBeNull();
    expect(screen.getAllByText(/MANUAL to recover/)).toHaveLength(1);
  });

  it("says the stop is ready, not the machine, beside a machine line that says Ready", async () => {
    fakeRuntime();

    await waitFor(() => expect(screen.getByTestId("machine-line")).toHaveTextContent("Ready."));
    expect(screen.getByTestId("estop-state")).toHaveTextContent(/^Stop ready$/);
  });
});
