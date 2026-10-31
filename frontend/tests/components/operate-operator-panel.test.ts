import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// wb#727: Operate opens on an operator panel whose first element is one line
// on what the machine is doing and what to do next. The software-stop row
// stays above it. Watchdog trips, Mode, Runtime Status, Event Stream and
// Provider Health follow, always shown; Devices, Parameters, Automation and
// the event trace each collapse on their own; Telemetry comes last, shown.
// A line's link scrolls to the section it names.

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

function scrollSpy(): Element[] {
  const scrolled: Element[] = [];
  Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
    scrolled.push(this);
  });
  return scrolled;
}

function heading(name: string): HTMLElement {
  return screen.getByRole("heading", { name, level: 3 });
}

describe("Operate.svelte — the operator panel (#727)", () => {
  it("shows the machine line, then the runtime's sections, none collapsed", async () => {
    fakeRuntime({ mode: "MANUAL", tripped: true });

    await waitFor(() => expect(screen.getByTestId("machine-line")).toHaveTextContent("tripped"));
    expect(screen.queryByTestId("engineering")).toBeNull();
    const shown = [
      screen.getByTestId("machine-line"),
      screen.getByTestId("watchdog-trips"),
      screen.getByTestId("mode-section"),
      screen.getByTestId("runtime-status"),
      heading("Event Stream"),
      screen.getByTestId("provider-health"),
    ];
    for (const el of shown) expect(el.closest("details")).toBeNull();
    for (let i = 1; i < shown.length; i += 1) {
      expect(
        shown[i - 1].compareDocumentPosition(shown[i]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it("collapses Devices, Parameters, Automation and the event trace, each on its own", async () => {
    fakeRuntime();

    await waitFor(() => expect(screen.getByTestId("machine-line")).toHaveTextContent("Ready."));
    const sections = [
      ["devices-section", "Devices"],
      ["parameters-section", "Parameters"],
      ["automation-section", "Automation"],
      ["event-trace-section", "Event Trace"],
    ].map(([id, name]) => {
      const el = screen.getByTestId(id) as HTMLDetailsElement;
      expect(el.tagName).toBe("DETAILS");
      expect(el.open).toBe(false);
      expect(el.querySelector(":scope > summary")).toHaveTextContent(name);
      return el;
    });
    expect(new Set(sections).size).toBe(4);
    for (const el of sections) expect(el.parentElement?.closest("details")).toBeNull();
    expect(
      heading("Provider Health").compareDocumentPosition(sections[0]) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows Telemetry last, not collapsed", async () => {
    fakeRuntime();

    await waitFor(() => expect(screen.getByTestId("machine-line")).toBeInTheDocument());
    const telemetry = heading("Telemetry");
    expect(telemetry.closest("details")).toBeNull();
    const sections = document.querySelectorAll("#workspace-operate .operate-section");
    expect(sections[sections.length - 1]).toBe(telemetry.closest(".operate-section"));
  });

  it("keeps the software stop above the panel, never collapsed", async () => {
    fakeRuntime({ latched: true });

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent(
        "Stopped by the software stop: clear it to continue.",
      ),
    );
    const stop = document.querySelector(".btn-estop");
    expect(stop).not.toBeNull();
    expect(stop?.closest("details")).toBeNull();
    expect(
      stop!.compareDocumentPosition(screen.getByTestId("machine-line")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("takes the operator to the trip clear when a device tripped", async () => {
    const scrolled = scrollSpy();
    fakeRuntime({ mode: "MANUAL", tripped: true });

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent(
        "Device tripped: clear the trip on bread0/dcmt0.",
      ),
    );
    await fireEvent.click(screen.getByRole("button", { name: "Clear the trip" }));

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

  it("takes the operator to Provider Health when a latched device is unreachable", async () => {
    const scrolled = scrollSpy();
    fakeRuntime({ mode: "MANUAL", lossLatched: ["ezo0/ph0"] });

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent(
        "Device lost: check its power (ezo0/ph0).",
      ),
    );
    await fireEvent.click(screen.getByRole("button", { name: "See Provider Health" }));

    await waitFor(() => expect(scrolled).toContain(screen.getByTestId("provider-health")));
  });

  it("takes the operator to Mode in FAULT", async () => {
    const scrolled = scrollSpy();
    fakeRuntime({ mode: "FAULT" });

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent("set MANUAL to recover"),
    );
    await fireEvent.click(screen.getByRole("button", { name: "Go to Mode" }));

    await waitFor(() => expect(scrolled).toContain(screen.getByTestId("mode-section")));
  });

  it("lands Mode below the sticky software stop, however tall the stop row is (#745)", async () => {
    scrollSpy();
    fakeRuntime({ mode: "FAULT" });
    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent("set MANUAL to recover"),
    );
    const stop = document.querySelector(".operate-estop-section") as HTMLElement;
    vi.spyOn(stop, "getBoundingClientRect").mockReturnValue({ height: 120 } as DOMRect);

    await fireEvent.click(screen.getByRole("button", { name: "Go to Mode" }));

    expect(screen.getByTestId("mode-section").style.scrollMarginTop).toBe("128px");
  });
});
