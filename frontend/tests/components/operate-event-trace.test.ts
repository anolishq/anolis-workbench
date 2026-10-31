import { render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// The runtime stamps each event with its own timestamp_ms, and events from
// one poll share a millisecond. The Event Trace must show each of them.

const RUNNING = { running: true, active_project: "demo" };

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

afterEach(() => {
  vi.unstubAllGlobals();
  ListeningEventSource.last = null;
});

describe("Operate.svelte — the Event Trace", () => {
  it("shows two events stamped in the same millisecond", async () => {
    vi.stubGlobal("EventSource", ListeningEventSource as unknown as typeof EventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        pathFromInput(input) === "/api/status"
          ? jsonResponse(200, RUNNING)
          : jsonResponse(200, {}),
      ),
    );
    render(Operate, {
      props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
    });
    await waitFor(() => expect(ListeningEventSource.last).not.toBeNull());

    const update = (signal: string, value: number) => ({
      provider_id: "sim0",
      device_id: "tempctl0",
      signal_id: signal,
      value: { type: "double", double: value },
      timestamp_ms: 1_700_000_000_000,
    });
    ListeningEventSource.last?.emit("state_update", update("tc1_temp", 25.5));
    ListeningEventSource.last?.emit("state_update", update("tc2_temp", 26.5));

    const trace = screen.getByTestId("event-trace-section");
    await waitFor(() => expect(trace).toHaveTextContent("sim0/tempctl0 tc2_temp = 26.5"));
    expect(within(trace).getByText("sim0/tempctl0 tc1_temp = 25.5")).toBeInTheDocument();
  });
});
