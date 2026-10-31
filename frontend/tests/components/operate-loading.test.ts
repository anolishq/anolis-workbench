import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, deferred, jsonResponse, pathFromInput } from "./helpers";

// wb#754: until the first runtime status has settled, the panel says it is
// connecting, not that the runtime is not answering, and Readings and
// Settings say they are loading. "Not answering" is kept for a status
// request that failed.

const RUNNING = { running: true, active_project: "demo" };
const OPERATOR = {
  readings: [{ signal: "ezo0/ph0/ph_value", label: "pH", decimals: 2 }],
  settings: [
    {
      group: "Stirring",
      items: [
        { parameter: "impeller_enable", label: "On" },
        { parameter: "impeller_pwm", label: "Speed" },
      ],
    },
  ],
  experiment: {
    start: { enable: ["impeller_enable"], mode: "AUTO" },
    stop: { disable: ["impeller_enable"], mode: "IDLE" },
  },
};

function renderOperate(status: Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      // Everything the panel reads waits on the status, as on the rig.
      if (path.startsWith("/v0/")) return status;
      return jsonResponse(200, {});
    }),
  );
  const doc = createProjectDocument("demo");
  doc.profile = { ...doc.profile, operator: OPERATOR };
  render(Operate, { props: { projectName: "demo", system: doc, runtimeStatus: RUNNING } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Operate.svelte — before the first status (wb#754)", () => {
  it("says it is connecting, and that Readings, Now and Settings are loading", async () => {
    const status = deferred<Response>();
    renderOperate(status.promise);

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent("Connecting to the runtime…"),
    );
    expect(screen.getByTestId("machine-line")).not.toHaveTextContent("not answering");
    const reading = screen.getByTestId("operator-reading");
    expect(reading).toHaveTextContent("loading");
    expect(reading).not.toHaveTextContent("no reading");
    expect(screen.getByTestId("operator-now")).toHaveTextContent("Loading…");
    expect(screen.getByTestId("operator-now")).not.toHaveTextContent("--");
    const settings = screen.getByTestId("operator-settings");
    expect(within(settings).getAllByText("loading…").length).toBeGreaterThan(0);
    expect(settings).not.toHaveTextContent("not reported by the runtime");
  });

  it("refuses Start while connecting, without saying the runtime is not answering", async () => {
    const status = deferred<Response>();
    renderOperate(status.promise);
    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent("Connecting to the runtime…"),
    );

    await fireEvent.click(screen.getByRole("button", { name: "Start experiment" }));

    const feedback = await screen.findByTestId("experiment-feedback");
    expect(feedback).toHaveTextContent("still connecting");
    expect(feedback).not.toHaveTextContent("not answering");
  });

  it("says the runtime is not answering once the status request has failed", async () => {
    const status = deferred<Response>();
    renderOperate(status.promise);
    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent("Connecting to the runtime…"),
    );

    status.resolve(jsonResponse(503, { status: { code: "UNAVAILABLE" } }));

    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).toHaveTextContent("not answering"),
    );
    expect(screen.getByTestId("operator-reading")).toHaveTextContent("no reading");
    expect(screen.getByTestId("operator-settings")).toHaveTextContent(
      "not reported by the runtime",
    );
  });
});
