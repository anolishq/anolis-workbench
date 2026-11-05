import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

const RUNNING = { running: true, active_project: "demo" };

function status(mode: string, latched: boolean) {
  return {
    status: { code: "OK" },
    version: "0.4.0",
    mode,
    uptime_seconds: 1,
    polling_interval_ms: 500,
    providers: [],
    device_count: 0,
    estop: {
      latched,
      software_safe_state: "hooks",
      latched_at_epoch_ms: latched ? 1_700_000_000_000 : null,
      uncovered_actuating_functions: 0,
    },
    device_loss_latched: [],
  };
}

/** Operate for a running `demo` whose runtime is in `mode`. */
function renderOperate(mode: string, latched = false) {
  let isLatched = latched;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      if (path === "/v0/runtime/status") return jsonResponse(200, status(mode, isLatched));
      if (path === "/v0/mode") return jsonResponse(200, { status: { code: "OK" }, mode });
      if (path === "/v0/devices") return jsonResponse(200, { devices: [] });
      if (path === "/v0/estop/clear" && init?.method === "POST") {
        isLatched = false;
        return jsonResponse(200, { latched: false });
      }
      return jsonResponse(200, {});
    }),
  );
  render(Operate, {
    props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
  });
}

/** The mode selector's options, once the badge shows `mode`. */
const modeOptions = async (mode: string) => {
  const badge = await screen.findByText(mode, { selector: '[data-testid="mode-badge"]' });
  const select = badge.closest(".operate-section")?.querySelector("select") as HTMLSelectElement;
  return Array.from(select.options).map((o) => o.value);
};
const row = () =>
  screen.getByRole("button", { name: /^SOFTWARE STOP/ }).closest(".estop-row") as HTMLElement;

describe("Operate.svelte — mode and latch wording (#692)", () => {
  it("in FAULT offers only the way out the runtime accepts, MANUAL, and the machine line says why", async () => {
    renderOperate("FAULT");
    expect(await modeOptions("FAULT")).toEqual(["MANUAL", "FAULT"]);
    // Said once, on the machine line (wb#748): Mode carries no note of its own.
    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent(
        "In FAULT: set MANUAL to recover (AUTO isn't available from FAULT).",
      ),
    );
    expect(screen.queryByTestId("mode-fault-note")).toBeNull();
  });

  it("in MANUAL offers every mode, and no FAULT note", async () => {
    renderOperate("MANUAL");
    expect(await modeOptions("MANUAL")).toEqual(["MANUAL", "AUTO", "IDLE", "FAULT"]);
    expect(screen.queryByTestId("mode-fault-note")).not.toBeInTheDocument();
  });

  it("clearing the latch in FAULT says device calls stay refused until MANUAL", async () => {
    renderOperate("FAULT", true);
    await screen.findByText("FAULT", { selector: '[data-testid="mode-badge"]' });
    await fireEvent.click(await within(row()).findByRole("button", { name: "Clear latch…" }));
    await fireEvent.click(
      within(row()).getByRole("button", { name: "Confirm clear: re-enables actuation" }),
    );

    const feedback = await within(row()).findByTestId("estop-feedback");
    expect(feedback).toHaveTextContent(
      "Latch cleared. The runtime is still in FAULT, which refuses every device call: set the mode to MANUAL.",
    );
    expect(feedback).not.toHaveTextContent("Actuation re-enabled");
  });
});

/**
 * Operate for a running `demo` whose runtime state the test changes from
 * outside the page: `rig.mode` and `rig.latched` are what the runtime reports
 * next, as another tab, the API or the hardware would leave them.
 */
function renderRig(rig: { mode: string; latched: boolean }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      if (path === "/v0/runtime/status") return jsonResponse(200, status(rig.mode, rig.latched));
      if (path === "/v0/mode" && init?.method === "POST") {
        rig.mode = JSON.parse(String(init.body)).mode;
        return jsonResponse(200, { status: { code: "OK" }, mode: rig.mode });
      }
      if (path === "/v0/mode") return jsonResponse(200, { status: { code: "OK" }, mode: rig.mode });
      if (path === "/v0/devices") return jsonResponse(200, { devices: [] });
      if (path === "/v0/estop/clear" && init?.method === "POST") {
        rig.latched = false;
        return jsonResponse(200, { latched: false });
      }
      return jsonResponse(200, {});
    }),
  );
  return render(Operate, {
    props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
  });
}

const FAULT_CLEARED =
  "Latch cleared. The runtime is still in FAULT, which refuses every device call: set the mode to MANUAL.";
const CLEARED = "Latch cleared. Actuation re-enabled.";

async function clearFromThePage(): Promise<void> {
  await fireEvent.click(await within(row()).findByRole("button", { name: "Clear latch…" }));
  await fireEvent.click(
    within(row()).getByRole("button", { name: "Confirm clear: re-enables actuation" }),
  );
}

async function setModeFromThePage(mode: string): Promise<void> {
  const badge = screen.getByTestId("mode-badge");
  const section = badge.closest(".operate-section") as HTMLElement;
  await fireEvent.change(section.querySelector("select") as HTMLSelectElement, {
    target: { value: mode },
  });
  await fireEvent.click(within(section).getByRole("button", { name: "Set Mode" }));
  await screen.findByText(mode, { selector: '[data-testid="mode-badge"]' });
}

/** The next 5 s status poll, which is how the page sees a change made elsewhere. */
const nextPoll = () => vi.advanceTimersByTimeAsync(5000);

describe("Operate.svelte — the latch-clear message follows the latch and mode (#725)", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("goes when a stop from outside the page latches again, and stays gone after that latch is released", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const rig = { mode: "FAULT", latched: true };
    renderRig(rig);
    await screen.findByText("FAULT", { selector: '[data-testid="mode-badge"]' });

    await clearFromThePage();
    expect(await within(row()).findByTestId("estop-feedback")).toHaveTextContent(FAULT_CLEARED);

    // A software stop from the API, another tab or the hardware.
    rig.latched = true;
    await nextPoll();
    expect(await within(row()).findByRole("button", { name: "Clear latch…" })).toBeInTheDocument();
    expect(within(row()).queryByTestId("estop-feedback")).not.toBeInTheDocument();

    await setModeFromThePage("MANUAL");
    expect(within(row()).queryByTestId("estop-feedback")).not.toBeInTheDocument();

    // Released elsewhere: that clear is not this page's to report.
    rig.latched = false;
    await nextPoll();
    await waitFor(() =>
      expect(within(row()).queryByRole("button", { name: "Clear latch…" })).not.toBeInTheDocument(),
    );
    expect(within(row()).queryByTestId("estop-feedback")).not.toBeInTheDocument();
  });

  it("names the mode the runtime is in now, however the mode changed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const rig = { mode: "FAULT", latched: true };
    renderRig(rig);
    await screen.findByText("FAULT", { selector: '[data-testid="mode-badge"]' });

    await clearFromThePage();
    expect(await within(row()).findByTestId("estop-feedback")).toHaveTextContent(FAULT_CLEARED);

    await setModeFromThePage("MANUAL");
    await waitFor(() => expect(within(row()).getByTestId("estop-feedback")).toHaveTextContent(CLEARED));
    expect(within(row()).getByTestId("estop-feedback")).not.toHaveTextContent("FAULT");

    // A fault raised outside the page: the note says FAULT again.
    rig.mode = "FAULT";
    await nextPoll();
    await waitFor(() =>
      expect(within(row()).getByTestId("estop-feedback")).toHaveTextContent(FAULT_CLEARED),
    );
  });

  it("does not come back with a runtime that stopped and started again", async () => {
    const rig = { mode: "MANUAL", latched: true };
    const view = renderRig(rig);
    await screen.findByText("MANUAL", { selector: '[data-testid="mode-badge"]' });
    await clearFromThePage();
    expect(await within(row()).findByTestId("estop-feedback")).toHaveTextContent(CLEARED);

    await view.rerender({ runtimeStatus: { running: false, active_project: null } });
    await waitFor(() => expect(screen.queryByRole("button", { name: /^SOFTWARE STOP/ })).toBeNull());
    await view.rerender({ runtimeStatus: RUNNING });
    await screen.findByText("MANUAL", { selector: '[data-testid="mode-badge"]' });

    expect(within(row()).queryByTestId("estop-feedback")).not.toBeInTheDocument();
  });
});
