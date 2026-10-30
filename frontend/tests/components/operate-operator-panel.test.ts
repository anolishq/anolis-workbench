import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// wb#727: Operate opens on an operator panel whose first element is one line
// on what the machine is doing and what to do next. Every section that was
// there before sits below it, collapsed under "Engineering"; the software-stop
// row stays above both, never collapsed. A line that points into a collapsed
// section opens "Engineering" and takes the operator there.

const RUNNING = { running: true, active_project: "demo" };

function fakeRuntime(
  opts: { mode?: string; latched?: boolean; tripped?: boolean; lossLatched?: string[] } = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      if (path === "/v0/runtime/status")
        return jsonResponse(200, {
          status: { code: "OK" },
          version: "0.6.0",
          mode: opts.mode ?? "IDLE",
          uptime_seconds: 1,
          polling_interval_ms: 500,
          providers: [],
          device_count: 1,
          device_loss_latched: opts.lossLatched ?? [],
          estop: {
            latched: opts.latched ?? false,
            software_safe_state: "hooks",
            latched_at_epoch_ms: opts.latched ? 1_700_000_000_000 : null,
            uncovered_actuating_functions: 0,
          },
        });
      if (path === "/v0/mode")
        return jsonResponse(200, { status: { code: "OK" }, mode: opts.mode ?? "IDLE" });
      if (path === "/v0/providers/health")
        return jsonResponse(200, {
          providers: [
            {
              provider_id: "bread0",
              state: "AVAILABLE",
              devices: [
                {
                  device_id: "dcmt0",
                  health: "OK",
                  reported: {
                    state: "OK",
                    metrics: { watchdog_tripped: String(opts.tripped ?? false) },
                  },
                },
              ],
            },
          ],
        });
      return jsonResponse(200, {});
    }),
  );
  render(Operate, {
    props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Operate.svelte — the operator panel (#727)", () => {
  it("opens on the machine line, with every other section collapsed under Engineering", async () => {
    fakeRuntime();

    await waitFor(() => expect(screen.getByTestId("machine-line")).toHaveTextContent("Ready."));
    const engineering = screen.getByTestId("engineering") as HTMLDetailsElement;
    expect(engineering.open).toBe(false);
    expect(within(engineering).getByText("Runtime Status")).toBeInTheDocument();
    expect(within(engineering).getByText("Provider Health")).toBeInTheDocument();
  });

  it("keeps the software stop above the panel, outside Engineering", async () => {
    fakeRuntime({ latched: true });

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent(
        "Stopped by the software stop: clear it to continue.",
      ),
    );
    const stop = document.querySelector(".btn-estop");
    expect(stop).not.toBeNull();
    expect(screen.getByTestId("engineering").contains(stop)).toBe(false);
  });

  it("takes the operator to the trip clear, opening Engineering, when a board tripped", async () => {
    const scrolled: Element[] = [];
    Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
      scrolled.push(this);
    });
    fakeRuntime({ mode: "MANUAL", tripped: true });

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent(
        "Board tripped: clear the trip on bread0/dcmt0.",
      ),
    );
    await fireEvent.click(screen.getByRole("button", { name: "Clear the trip" }));

    const engineering = screen.getByTestId("engineering") as HTMLDetailsElement;
    await waitFor(() => expect(engineering.open).toBe(true));
    await waitFor(() => expect(scrolled).toContain(screen.getByTestId("watchdog-trips")));
  });

  it("does not call a device lost once provider health has it back, while it stays latched", async () => {
    fakeRuntime({ mode: "MANUAL", lossLatched: ["bread0/dcmt0"] });

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent(
        "bread0/dcmt0 came back after a loss. Automation won't drive them until you go MANUAL → AUTO.",
      ),
    );
    expect(screen.getByTestId("machine-line")).not.toHaveTextContent("power");
  });

  it("takes the operator to Mode, opening Engineering, in FAULT", async () => {
    const scrolled: Element[] = [];
    Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
      scrolled.push(this);
    });
    fakeRuntime({ mode: "FAULT" });

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent("set MANUAL to recover"),
    );
    await fireEvent.click(screen.getByRole("button", { name: "Go to Mode" }));

    await waitFor(() =>
      expect((screen.getByTestId("engineering") as HTMLDetailsElement).open).toBe(true),
    );
    await waitFor(() => expect(scrolled).toContain(screen.getByTestId("mode-section")));
  });
});
