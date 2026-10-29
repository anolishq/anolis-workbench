import { render, screen } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

const RUNNING = { running: true, active_project: "demo" };

afterEach(() => vi.unstubAllGlobals());

describe("Operate.svelte — telemetry is a link, not a frame (anolis#646)", () => {
  it("links to Grafana and embeds nothing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === "/api/status") return jsonResponse(200, RUNNING);
        if (path === "/api/config")
          return jsonResponse(200, { telemetry_url: "http://localhost:3000/" });
        return jsonResponse(200, {});
      }),
    );
    const { container } = render(Operate, {
      props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
    });

    const link = await screen.findByRole("link", { name: "Open Grafana in new tab →" });
    expect(link).toHaveAttribute("href", "http://localhost:3000");
    // install.sh's Grafana sends X-Frame-Options: deny, so a frame is always blank.
    expect(container.querySelector("iframe")).toBeNull();
  });
});
