import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, deferred, jsonResponse, pathFromInput } from "./helpers";

// A poll reads /v0/mode alongside six other requests and applies it only once
// they all settle. A mode_change event that lands in between is newer, and
// the poll's reply must not undo it (wb#818).

const RUNNING = { running: true, active_project: "demo" };

function status(mode: string) {
  return {
    status: { code: "OK" },
    version: "0.4.0",
    mode,
    uptime_seconds: 1,
    polling_interval_ms: 500,
    providers: [],
    device_count: 0,
    estop: {
      latched: false,
      software_safe_state: "hooks",
      latched_at_epoch_ms: null,
      uncovered_actuating_functions: 0,
    },
    device_loss_latched: [],
  };
}

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

const badge = () => screen.getByTestId("mode-badge");

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  ListeningEventSource.last = null;
});

describe("Operate.svelte — a poll's mode reply against a newer mode_change (wb#818)", () => {
  it("keeps the mode from a mode_change that arrived while the poll was in flight", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubGlobal("EventSource", ListeningEventSource as unknown as typeof EventSource);
    const rig = { mode: "MANUAL", held: null as ReturnType<typeof deferred<Response>> | null };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === "/api/status") return jsonResponse(200, RUNNING);
        if (path === "/v0/mode")
          return jsonResponse(200, { status: { code: "OK" }, mode: rig.mode });
        if (path === "/v0/runtime/status") {
          if (rig.held) return rig.held.promise;
          return jsonResponse(200, status(rig.mode));
        }
        if (path === "/v0/devices") return jsonResponse(200, { devices: [] });
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
    });
    await screen.findByText("MANUAL", { selector: '[data-testid="mode-badge"]' });
    await waitFor(() => expect(ListeningEventSource.last).not.toBeNull());

    // The next poll's mode reply answers MANUAL; its status reply is held.
    const held = deferred<Response>();
    rig.held = held;
    await vi.advanceTimersByTimeAsync(5000);

    // The runtime faults, and the event says so before the poll settles.
    rig.mode = "FAULT";
    ListeningEventSource.last?.emit("mode_change", { previous_mode: "MANUAL", new_mode: "FAULT" });
    await waitFor(() => expect(badge()).toHaveTextContent("FAULT"));

    rig.held = null;
    held.resolve(jsonResponse(200, status("FAULT")));
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();

    expect(badge()).toHaveTextContent("FAULT");
  });

  // Review of #891: a set mode outranks a poll that left before it, with
  // no event stream to say so.
  it("keeps a mode this page set over a poll that left before the set", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubGlobal("EventSource", ListeningEventSource as unknown as typeof EventSource);
    const rig = { mode: "MANUAL", held: null as ReturnType<typeof deferred<Response>> | null };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (path === "/api/status") return jsonResponse(200, RUNNING);
        if (path === "/v0/mode" && init?.method === "POST") {
          rig.mode = JSON.parse(String(init.body)).mode;
          return jsonResponse(200, { status: { code: "OK" }, mode: rig.mode });
        }
        if (path === "/v0/mode")
          return jsonResponse(200, { status: { code: "OK" }, mode: rig.mode });
        if (path === "/v0/runtime/status") {
          if (rig.held) return rig.held.promise;
          return jsonResponse(200, status(rig.mode));
        }
        if (path === "/v0/devices") return jsonResponse(200, { devices: [] });
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
    });
    await screen.findByText("MANUAL", { selector: '[data-testid="mode-badge"]' });

    // The next poll's mode reply answers MANUAL; its status reply is held.
    const held = deferred<Response>();
    rig.held = held;
    await vi.advanceTimersByTimeAsync(5000);

    const section = screen.getByTestId("mode-section");
    await fireEvent.change(within(section).getByRole("combobox"), { target: { value: "IDLE" } });
    await fireEvent.click(within(section).getByRole("button", { name: "Set Mode" }));
    await waitFor(() => expect(badge()).toHaveTextContent("IDLE"));

    rig.held = null;
    held.resolve(jsonResponse(200, status("IDLE")));
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();

    expect(badge()).toHaveTextContent("IDLE");
  });

  // Review of #891: refreshModeOnly's own guard. A mode_change that lands
  // while the set's follow-up read is in flight is newer than that read.
  it("keeps a mode_change that lands while the set's follow-up read is in flight", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubGlobal("EventSource", ListeningEventSource as unknown as typeof EventSource);
    const rig = { mode: "MANUAL", heldRead: null as ReturnType<typeof deferred<Response>> | null };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (path === "/api/status") return jsonResponse(200, RUNNING);
        if (path === "/v0/mode" && init?.method === "POST") {
          rig.mode = JSON.parse(String(init.body)).mode;
          return jsonResponse(200, { status: { code: "OK" }, mode: rig.mode });
        }
        if (path === "/v0/mode") {
          if (rig.heldRead) return rig.heldRead.promise;
          return jsonResponse(200, { status: { code: "OK" }, mode: rig.mode });
        }
        if (path === "/v0/runtime/status") return jsonResponse(200, status(rig.mode));
        if (path === "/v0/devices") return jsonResponse(200, { devices: [] });
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
    });
    await screen.findByText("MANUAL", { selector: '[data-testid="mode-badge"]' });
    await waitFor(() => expect(ListeningEventSource.last).not.toBeNull());

    const heldRead = deferred<Response>();
    rig.heldRead = heldRead;
    const section = screen.getByTestId("mode-section");
    await fireEvent.change(within(section).getByRole("combobox"), { target: { value: "IDLE" } });
    await fireEvent.click(within(section).getByRole("button", { name: "Set Mode" }));

    ListeningEventSource.last?.emit("mode_change", { previous_mode: "IDLE", new_mode: "FAULT" });
    await waitFor(() => expect(badge()).toHaveTextContent("FAULT"));

    rig.heldRead = null;
    heldRead.resolve(jsonResponse(200, { status: { code: "OK" }, mode: "IDLE" }));
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();

    expect(badge()).toHaveTextContent("FAULT");
  });
});
