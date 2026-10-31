import { render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// wb#747: a sparkline beside each declared reading, from the state updates
// and polls Operate already gets. Empty on load, no backfill; a point is
// added only when the reading's own timestamp moves; a stale point is a gap.

const RUNNING = { running: true, active_project: "demo" };
const OPERATOR = { readings: [{ signal: "ezo0/ph0/ph_value", label: "pH", decimals: 2 }] };

function fakeRuntime(next: () => { value: number; quality: string; stamp: number }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
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
          device_count: 1,
          device_loss_latched: [],
          estop: {
            latched: false,
            software_safe_state: "hooks",
            latched_at_epoch_ms: null,
            uncovered_actuating_functions: 0,
          },
        });
      if (path === "/v0/devices")
        return jsonResponse(200, {
          devices: [{ provider_id: "ezo0", device_id: "ph0", display_name: "ph0", type: "ezo" }],
        });
      if (path === "/v0/state/ezo0/ph0") {
        const s = next();
        return jsonResponse(200, {
          status: { code: "OK" },
          provider_id: "ezo0",
          device_id: "ph0",
          quality: "OK",
          values: [
            {
              signal_id: "ph_value",
              value: { type: "double", double: s.value },
              quality: s.quality,
              timestamp_epoch_ms: s.stamp,
              age_ms: 5,
            },
          ],
        });
      }
      return jsonResponse(200, {});
    }),
  );
  const doc = createProjectDocument("demo");
  doc.profile = { ...doc.profile, operator: OPERATOR };
  render(Operate, { props: { projectName: "demo", system: doc, runtimeStatus: RUNNING } });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const line = () =>
  within(screen.getByTestId("operator-reading"))
    .getByTestId("sparkline")
    .querySelector("path") as SVGPathElement;

describe("Operate.svelte — a sparkline beside each reading (wb#747)", () => {
  it("starts empty and draws a line once the reading has moved", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const reads = [
      { value: 6.5, quality: "OK", stamp: 1000 },
      { value: 6.7, quality: "OK", stamp: 6000 },
    ];
    let i = 0;
    fakeRuntime(() => reads[Math.min(i++, reads.length - 1)]);

    await waitFor(() => expect(screen.getByTestId("operator-reading")).toHaveTextContent("6.50"));
    expect(line().getAttribute("d")).toBe("");
    await vi.advanceTimersByTimeAsync(5000);

    await waitFor(() => expect(line().getAttribute("d")).toMatch(/^M\S+ \S+ L\S+ \S+$/));
  });

  it("adds no point while the reading's own timestamp stands still", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fakeRuntime(() => ({ value: 6.5, quality: "OK", stamp: 1000 }));

    await waitFor(() => expect(screen.getByTestId("operator-reading")).toHaveTextContent("6.50"));
    await vi.advanceTimersByTimeAsync(15000);

    expect(line().getAttribute("d")).toBe("");
  });

  it("leaves a gap where the reading was stale", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const reads = [
      { value: 6.5, quality: "OK", stamp: 1000 },
      { value: 6.6, quality: "OK", stamp: 2000 },
      { value: 6.6, quality: "STALE", stamp: 3000 },
      { value: 6.8, quality: "OK", stamp: 4000 },
      { value: 6.9, quality: "OK", stamp: 5000 },
    ];
    let i = 0;
    fakeRuntime(() => reads[Math.min(i++, reads.length - 1)]);

    await waitFor(() => expect(screen.getByTestId("operator-reading")).toHaveTextContent("6.50"));
    for (let n = 0; n < 4; n += 1) await vi.advanceTimersByTimeAsync(5000);

    await waitFor(() => expect(line().getAttribute("d")?.match(/M/g)).toHaveLength(2));
  });
});
