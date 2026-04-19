import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_SSE_EVENT_TYPES,
  appendEventTrace,
  buildTraceEvent,
  createOperateEventStreamManager,
  describeEvent,
} from "../../src/lib/operate-events";

type Listener = (event: { data: string }) => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static shouldThrow = false;

  readonly url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  private readonly listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    if (FakeEventSource.shouldThrow) {
      throw new Error("init failed");
    }
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: Listener) {
    const handlers = this.listeners.get(type) ?? [];
    handlers.push(handler);
    this.listeners.set(type, handlers);
  }

  emit(type: string, payload: unknown) {
    const handlers = this.listeners.get(type) ?? [];
    const event = {
      data: typeof payload === "string" ? payload : JSON.stringify(payload),
    };
    for (const handler of handlers) {
      handler(event);
    }
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  fail() {
    this.readyState = 2;
    this.onerror?.();
  }

  close() {
    this.readyState = 2;
    this.closed = true;
  }
}

describe("operate-events helpers", () => {
  it("exposes expected default SSE event types", () => {
    expect(DEFAULT_SSE_EVENT_TYPES).toEqual([
      "state_update",
      "quality_change",
      "mode_change",
      "parameter_change",
      "bt_error",
      "provider_health_change",
    ]);
  });

  it("appendEventTrace keeps buffer bounded", () => {
    const buffer: Array<{ type: string }> = [];
    appendEventTrace(buffer, { type: "a" }, 3);
    appendEventTrace(buffer, { type: "b" }, 3);
    appendEventTrace(buffer, { type: "c" }, 3);
    appendEventTrace(buffer, { type: "d" }, 3);
    expect(buffer.map((entry) => entry.type)).toEqual(["b", "c", "d"]);
  });

  it("describeEvent renders human-readable summaries for known event families", () => {
    expect(describeEvent("mode_change", { previous_mode: "IDLE", new_mode: "ACTIVE" })).toBe(
      "IDLE -> ACTIVE",
    );
    expect(
      describeEvent("parameter_change", {
        parameter_name: "target_temp",
        old_value: 20,
        new_value: 21,
      }),
    ).toBe("target_temp: 20 -> 21");
    expect(describeEvent("bt_error", { node: "ActionA", error: "boom" })).toBe("ActionA: boom");
    expect(describeEvent("provider_health_change", { provider_id: "sim0", state: "FAULT" })).toBe(
      "sim0: FAULT",
    );
    expect(
      describeEvent("quality_change", {
        provider_id: "sim0",
        device_id: "dev0",
        signal_id: "temp",
        new_quality: "OK",
      }),
    ).toBe("sim0/dev0 temp -> OK");
  });

  it("buildTraceEvent prefers payload timestamp_ms when present", () => {
    const event = buildTraceEvent("state_update", { timestamp_ms: 500, provider_id: "sim0" }, 1000);
    expect(event.timestamp_ms).toBe(500);
    expect(event.type).toBe("state_update");
  });
});

describe("createOperateEventStreamManager", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    FakeEventSource.shouldThrow = false;
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00.000Z"));
    vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("handles connect, stale, reconnect, and disconnect transitions", () => {
    const statuses: Array<{ state: string; attempts: number }> = [];
    const events: Array<{ eventType: string; payload: unknown }> = [];

    const manager = createOperateEventStreamManager({
      onConnectionStatus: (status) => statuses.push(status),
      onEvent: (eventType, payload) => events.push({ eventType, payload }),
      reconnectDelayMs: 200,
      maxReconnectDelayMs: 1000,
      staleAfterMs: 1000,
      staleCheckIntervalMs: 100,
    });

    manager.connect();
    expect(FakeEventSource.instances).toHaveLength(1);

    const stream1 = FakeEventSource.instances[0];
    stream1.open();
    expect(statuses.at(-1)?.state).toBe("connected");
    expect(manager.getState()).toBe("connected");

    stream1.emit("state_update", {
      provider_id: "sim0",
      device_id: "dev0",
      signal_id: "temp",
      timestamp_ms: 10,
    });
    expect(events).toHaveLength(1);

    vi.advanceTimersByTime(1200);
    expect(statuses.at(-1)?.state).toBe("stale");

    stream1.emit("quality_change", {
      provider_id: "sim0",
      device_id: "dev0",
      signal_id: "temp",
      new_quality: "OK",
      timestamp_ms: 1200,
    });
    expect(statuses.at(-1)?.state).toBe("connected");

    stream1.fail();
    expect(statuses.at(-1)?.state).toBe("reconnecting");

    vi.advanceTimersByTime(200);
    expect(FakeEventSource.instances).toHaveLength(2);

    const stream2 = FakeEventSource.instances[1];
    stream2.open();
    expect(statuses.at(-1)?.state).toBe("connected");

    manager.disconnect();
    expect(stream2.closed).toBe(true);
    expect(statuses.at(-1)?.state).toBe("disconnected");
    expect(manager.getState()).toBe("disconnected");
  });

  it("routes JSON parse failures to onParseError", () => {
    const parseErrors: Array<{ context: string; message: string }> = [];
    const events: Array<{ eventType: string; payload: unknown }> = [];
    const manager = createOperateEventStreamManager({
      onParseError: (error, context) =>
        parseErrors.push({
          context,
          message: error instanceof Error ? error.message : String(error),
        }),
      onEvent: (eventType, payload) => events.push({ eventType, payload }),
    });

    manager.connect();
    const stream = FakeEventSource.instances[0];
    stream.open();
    stream.emit("state_update", "{invalid-json");

    expect(parseErrors).toHaveLength(1);
    expect(parseErrors[0].context).toBe("state_update");
    expect(events).toHaveLength(0);

    manager.disconnect();
  });

  it("handles EventSource construction failures and emits reconnecting state", () => {
    FakeEventSource.shouldThrow = true;
    const parseErrors: Array<{ context: string; message: string }> = [];
    const statuses: Array<{ state: string; attempts: number }> = [];

    const manager = createOperateEventStreamManager({
      reconnectDelayMs: 100,
      onConnectionStatus: (status) => statuses.push(status),
      onParseError: (error, context) =>
        parseErrors.push({
          context,
          message: error instanceof Error ? error.message : String(error),
        }),
    });

    manager.connect();
    expect(parseErrors).toHaveLength(1);
    expect(parseErrors[0].context).toBe("event_source_init");
    expect(statuses.at(-1)?.state).toBe("reconnecting");
    expect(manager.getState()).toBe("reconnecting");

    manager.disconnect();
  });
});
