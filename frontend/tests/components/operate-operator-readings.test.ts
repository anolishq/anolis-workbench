import { render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// wb#728: the operator panel shows the profile's declared readings, large,
// with label, unit and declared decimals, and says when one is stale or
// faulted. Below them, a line per settings group from the declared settings:
// a group whose enable parameter is true reads as on, which says it is
// scheduled, not that its actuator is running.

const RUNNING = { running: true, active_project: "demo" };

const OPERATOR = {
  readings: [
    { signal: "ezo0/ph0/ph_value", label: "pH", decimals: 2 },
    { signal: "ezo0/rtd0/temp_c", label: "Temperature", unit: "°C", decimals: 1 },
    { signal: "ezo0/do0/do_mg_l", label: "Dissolved oxygen", unit: "mg/L" },
  ],
  settings: [
    {
      group: "Stirring",
      items: [
        { parameter: "impeller_enable", label: "On" },
        { parameter: "impeller_pwm", label: "Speed" },
      ],
    },
    {
      group: "Dose 1",
      items: [
        { parameter: "dose1_enable", label: "On" },
        { parameter: "dose1_interval_s", label: "Every", unit: "s" },
        { parameter: "dose1_pulse_s", label: "For", unit: "s" },
      ],
    },
    { group: "Limits", items: [{ parameter: "temp_max_c", label: "Max", unit: "°C" }] },
  ],
  experiment: {
    start: { enable: ["impeller_enable", "dose1_enable"], mode: "AUTO" },
    stop: { disable: ["impeller_enable", "dose1_enable"], mode: "IDLE" },
  },
};

const PARAMETERS = [
  { name: "impeller_enable", type: "bool", value: false },
  { name: "impeller_pwm", type: "int64", value: 80 },
  { name: "dose1_enable", type: "bool", value: true },
  { name: "dose1_interval_s", type: "int64", value: 20 },
  { name: "dose1_pulse_s", type: "int64", value: 3 },
  { name: "temp_max_c", type: "double", value: 37.5 },
];

type Signal = { signal_id: string; value: unknown; quality: string };

const STATES: Record<string, Signal[]> = {
  "ezo0/ph0": [
    { signal_id: "ph_value", value: { type: "double", double: 5.409000000000001 }, quality: "OK" },
  ],
  "ezo0/rtd0": [{ signal_id: "temp_c", value: { type: "double", double: 30.04 }, quality: "OK" }],
  "ezo0/do0": [{ signal_id: "do_mg_l", value: { type: "double", double: 7.25 }, quality: "OK" }],
};

function fakeRuntime(
  opts: { operator?: unknown; states?: Record<string, Signal[]>; failState?: string } = {},
) {
  const states = opts.states ?? STATES;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      if (path === "/v0/runtime/status")
        return jsonResponse(200, {
          status: { code: "OK" },
          version: "0.7.0",
          mode: "AUTO",
          uptime_seconds: 1,
          polling_interval_ms: 500,
          providers: [],
          device_count: 3,
          device_loss_latched: [],
          estop: {
            latched: false,
            software_safe_state: "hooks",
            latched_at_epoch_ms: null,
            uncovered_actuating_functions: 0,
          },
        });
      if (path === "/v0/mode") return jsonResponse(200, { status: { code: "OK" }, mode: "AUTO" });
      if (path === "/v0/parameters")
        return jsonResponse(200, { status: { code: "OK" }, parameters: PARAMETERS });
      if (path === "/v0/devices")
        return jsonResponse(200, {
          devices: ["ph0", "rtd0", "do0"].map((d) => ({
            provider_id: "ezo0",
            device_id: d,
            display_name: d,
            type: "ezo",
          })),
        });
      const state = path.match(/^\/v0\/state\/([^/]+)\/([^/]+)$/);
      if (state) {
        const key = `${state[1]}/${state[2]}`;
        if (key === opts.failState) return jsonResponse(503, { status: { code: "UNAVAILABLE" } });
        return jsonResponse(200, {
          status: { code: "OK" },
          provider_id: state[1],
          device_id: state[2],
          quality: "OK",
          values: (states[key] ?? []).map((s) => ({
            ...s,
            timestamp_epoch_ms: 1_700_000_000_000,
            age_ms: 10,
          })),
        });
      }
      return jsonResponse(200, {});
    }),
  );
  const doc = createProjectDocument("demo");
  if (opts.operator !== null) doc.profile = { ...doc.profile, operator: opts.operator ?? OPERATOR };
  render(Operate, { props: { projectName: "demo", system: doc, runtimeStatus: RUNNING } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function reading(label: string): HTMLElement {
  return within(screen.getByTestId("operator-readings"))
    .getByText(label)
    .closest("[data-testid='operator-reading']") as HTMLElement;
}

describe("Operate.svelte — the operator panel's readings (#728)", () => {
  it("shows each declared reading with its label, unit and decimals, above Mode, always shown", async () => {
    fakeRuntime();

    await waitFor(() => expect(reading("pH")).toHaveTextContent("5.41"));
    expect(reading("pH")).not.toHaveTextContent("5.409");
    await waitFor(() => expect(reading("Temperature")).toHaveTextContent("30.0 °C"));
    // No decimals declared: the value as the runtime sent it.
    await waitFor(() => expect(reading("Dissolved oxygen")).toHaveTextContent("7.25 mg/L"));

    const readings = screen.getByTestId("operator-readings");
    expect(readings.closest("details")).toBeNull();
    expect(
      readings.compareDocumentPosition(screen.getByTestId("mode-section")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    const labels = within(readings)
      .getAllByTestId("operator-reading")
      .map((el) => el.querySelector(".operator-reading-label")?.textContent);
    expect(labels).toEqual(["pH", "Temperature", "Dissolved oxygen"]);
  });

  it("says a stale reading is stale and a faulted one is faulted", async () => {
    fakeRuntime({
      states: {
        ...STATES,
        "ezo0/ph0": [
          { signal_id: "ph_value", value: { type: "double", double: 5.4 }, quality: "STALE" },
        ],
        "ezo0/rtd0": [
          { signal_id: "temp_c", value: { type: "double", double: 30 }, quality: "FAULT" },
        ],
      },
    });

    await waitFor(() => expect(reading("pH")).toHaveTextContent("stale"));
    await waitFor(() => expect(reading("Temperature")).toHaveTextContent("faulted"));
    await waitFor(() => expect(reading("Dissolved oxygen")).toHaveTextContent("7.25"));
    expect(reading("Dissolved oxygen")).not.toHaveTextContent(/stale|faulted|no reading/);
  });

  it("says a reading the runtime has not reported has no reading", async () => {
    fakeRuntime({ failState: "ezo0/do0" });

    await waitFor(() => expect(reading("Temperature")).toHaveTextContent("30.0"));
    // Once the first status has settled; before it, a missing reading is
    // loading (wb#754).
    await waitFor(() => expect(reading("Dissolved oxygen")).toHaveTextContent("no reading"));
    expect(reading("Dissolved oxygen")).not.toHaveTextContent("7.25");
  });

  it("says what is happening per group, on with its main value or off, not the schedule (wb#748)", async () => {
    fakeRuntime();

    const now = await screen.findByTestId("operator-now");
    await waitFor(() => expect(now).toHaveTextContent("Dose 1: on, Every 20 s"));
    expect(now).not.toHaveTextContent("For 3 s");
    expect(now).toHaveTextContent("Stirring: off");
    expect(now).not.toHaveTextContent("Speed");
    // A group with no enable among experiment.start.enable says nothing here.
    expect(now).not.toHaveTextContent("Limits");
    expect(now.closest("details")).toBeNull();
    expect(
      now.compareDocumentPosition(screen.getByTestId("mode-section")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows neither without an operator block", async () => {
    fakeRuntime({ operator: null });

    await waitFor(() => expect(screen.getByTestId("machine-line")).toHaveTextContent("running"));
    expect(screen.queryByTestId("operator-readings")).toBeNull();
    expect(screen.queryByTestId("operator-now")).toBeNull();
  });
});
