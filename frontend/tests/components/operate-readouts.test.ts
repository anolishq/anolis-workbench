import { render, screen, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

const RUNNING = { running: true, active_project: "demo" };
const double = (v: number) => ({ type: "double", double: v });

function renderOperate() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      if (path === "/v0/devices")
        return jsonResponse(200, { devices: [{ provider_id: "bread0", device_id: "dcmt0" }] });
      if (path === "/v0/state/bread0/dcmt0")
        return jsonResponse(200, {
          values: [
            { signal_id: "period2_ms", value: double(20), quality: "OK", timestamp_ms: 1 },
            { signal_id: "period10_ms", value: double(100), quality: "OK", timestamp_ms: 1 },
            { signal_id: "period1_ms", value: double(10), quality: "OK", timestamp_ms: 1 },
          ],
        });
      if (path === "/v0/devices/bread0/dcmt0/capabilities")
        return jsonResponse(200, {
          capabilities: {
            signals: [],
            functions: [
              {
                function_id: 1,
                name: "set_offset",
                args: { offset: { type: "double", required: true, min: -3276.8, max: 3276.7 } },
              },
            ],
          },
        });
      return jsonResponse(200, {});
    }),
  );
  render(Operate, {
    props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("Operate.svelte — signal and argument readouts (wb#692)", () => {
  it("lists a device's signals by name, numbers in order", async () => {
    renderOperate();
    const cell = await screen.findByText("period10_ms");
    const rows = within(cell.closest("tbody") as HTMLElement).getAllByRole("row");
    expect(rows.map((r) => r.querySelector("td")?.textContent)).toEqual([
      "period1_ms",
      "period2_ms",
      "period10_ms",
    ]);
  });

  it("writes an argument's range so a negative bound reads unambiguously", async () => {
    renderOperate();
    expect(await screen.findByText("[-3276.8 to 3276.7]")).toBeInTheDocument();
  });
});
