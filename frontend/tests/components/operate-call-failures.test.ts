import { render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// anolis#678: automation calls a device refuses or fails used to go unseen
// while AUTO ran on. anolis 0.8.0 lists them in /v0/automation/status
// (call_failures) and sends automation_call_failed for each one. The Now
// section says which call is failing, how many times in a row, and the last
// error, between status polls as well.

const RUNNING = { running: true, active_project: "demo" };

const OPERATOR = {
  settings: [{ group: "Dose 1", items: [{ parameter: "dose1_enable", label: "On" }] }],
  experiment: { start: { enable: ["dose1_enable"], mode: "AUTO" } },
};

type CallFailure = {
  device_handle: string;
  function: string;
  consecutive_failures: number;
  last_error: string;
  last_failure_at_epoch_ms: number;
};

const FAILING: CallFailure = {
  device_handle: "bread0/dcmt1",
  function: "pulse_motor",
  consecutive_failures: 2,
  last_error: "motor1 brake engaged",
  last_failure_at_epoch_ms: 1_700_000_000_000,
};

const RECOVERED: CallFailure = {
  device_handle: "bread0/dcmt0",
  function: "set_pwm",
  consecutive_failures: 0,
  last_error: "timeout",
  last_failure_at_epoch_ms: 1_699_999_990_000,
};

/** An EventSource that records its listeners, so the test can deliver a named event. */
class ListeningEventSource {
  static last: ListeningEventSource | null = null;
  listeners: Record<string, (event: MessageEvent<string>) => void> = {};
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  constructor(public url: string) {
    ListeningEventSource.last = this;
  }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    this.listeners[type] = listener;
  }
  removeEventListener(): void {}
  close(): void {}
  emit(type: string, data: unknown): void {
    this.listeners[type]?.(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
}

/** `callFailures` undefined leaves the field out, as runtimes before 0.8.0 do. */
function fakeRuntime(opts: { operator?: unknown; callFailures?: CallFailure[] } = {}) {
  vi.stubGlobal("EventSource", ListeningEventSource as unknown as typeof EventSource);
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = pathFromInput(input);
    if (path === "/api/status") return jsonResponse(200, RUNNING);
    if (path === "/v0/runtime/status")
      return jsonResponse(200, {
        status: { code: "OK" },
        version: "0.8.0",
        mode: "AUTO",
        uptime_seconds: 1,
        polling_interval_ms: 500,
        providers: [],
        device_count: 2,
        device_loss_latched: [],
      });
    if (path === "/v0/mode") return jsonResponse(200, { status: { code: "OK" }, mode: "AUTO" });
    if (path === "/v0/parameters")
      return jsonResponse(200, {
        status: { code: "OK" },
        parameters: [{ name: "dose1_enable", type: "bool", value: true }],
      });
    if (path === "/v0/automation/status")
      return jsonResponse(200, {
        status: { code: "OK" },
        execution_status: "running",
        execution_reason: null,
        automation_version: null,
        last_evaluation_at_epoch_ms: null,
        run_id: null,
        last_error: null,
        ...(opts.callFailures ? { call_failures: opts.callFailures } : {}),
      });
    return jsonResponse(200, {});
  });
  vi.stubGlobal("fetch", fetchMock);
  const doc = createProjectDocument("demo");
  doc.profile = { ...doc.profile, operator: opts.operator ?? OPERATOR };
  render(Operate, { props: { projectName: "demo", system: doc, runtimeStatus: RUNNING } });
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  ListeningEventSource.last = null;
});

function failureLines(): string[] {
  return within(screen.getByTestId("operator-now"))
    .queryAllByTestId("call-failure")
    .map((li) => li.textContent ?? "");
}

function statusReads(fetchMock: ReturnType<typeof vi.fn>): number {
  return fetchMock.mock.calls.filter(([input]) => pathFromInput(input) === "/v0/automation/status")
    .length;
}

describe("Operate.svelte — failing automation calls in Now (anolis#678)", () => {
  it("names each failing call from status, its failures in a row and its last error", async () => {
    fakeRuntime({ callFailures: [FAILING, RECOVERED] });

    await waitFor(() =>
      expect(failureLines()).toEqual([
        "bread0/dcmt1 pulse_motor: failed 2 times in a row; last error: motor1 brake engaged",
      ]),
    );
    // A call that has succeeded since its last failure is not failing.
    expect(screen.getByTestId("operator-now")).not.toHaveTextContent("set_pwm");
    expect(screen.getByTestId("operator-now")).toHaveTextContent("Dose 1: on");
  });

  it("updates from automation_call_failed without waiting for the next status poll", async () => {
    const fetchMock = fakeRuntime({ callFailures: [] });
    await waitFor(() => expect(screen.getByTestId("operator-now")).toHaveTextContent("Dose 1: on"));
    await waitFor(() => expect(ListeningEventSource.last).not.toBeNull());
    const reads = statusReads(fetchMock);
    expect(failureLines()).toEqual([]);

    const event = {
      device_handle: "bread0/dcmt1",
      function: "pulse_motor",
      error: "motor1 brake engaged",
      consecutive_failures: 1,
      timestamp_ms: 1_700_000_000_000,
    };
    ListeningEventSource.last?.emit("automation_call_failed", event);
    await waitFor(() =>
      expect(failureLines()).toEqual([
        "bread0/dcmt1 pulse_motor: failed once; last error: motor1 brake engaged",
      ]),
    );

    ListeningEventSource.last?.emit("automation_call_failed", {
      ...event,
      error: "Provider not found: bread0",
      consecutive_failures: 2,
    });
    await waitFor(() =>
      expect(failureLines()).toEqual([
        "bread0/dcmt1 pulse_motor: failed 2 times in a row; last error: Provider not found: bread0",
      ]),
    );
    expect(statusReads(fetchMock)).toBe(reads);
  });

  it("shows Now for a failing call when the profile declares no settings", async () => {
    fakeRuntime({
      operator: { readings: [{ signal: "ezo0/ph0/ph_value", label: "pH" }] },
      callFailures: [FAILING],
    });

    await waitFor(() =>
      expect(failureLines()).toEqual([
        "bread0/dcmt1 pulse_motor: failed 2 times in a row; last error: motor1 brake engaged",
      ]),
    );
  });

  it("shows no failing call for a runtime older than 0.8.0, which sends no call_failures", async () => {
    fakeRuntime();

    await waitFor(() => expect(screen.getByTestId("operator-now")).toHaveTextContent("Dose 1: on"));
    expect(failureLines()).toEqual([]);
  });
});
