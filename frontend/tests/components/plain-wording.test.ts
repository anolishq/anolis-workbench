import { render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import ProfileView from "../../src/lib/ProfileView.svelte";
import Commission from "../../src/routes/Commission.svelte";
import { createProjectDocument, createRuntimeStatus, jsonResponse, pathFromInput } from "./helpers";

// Facts the UI showed raw, now said in plain words (#574).

function profileDoc(estop_topology: string) {
  return {
    meta: { imported_from: "/home/me/m", read_only: true },
    profile: { machine_id: "m1", safety: { estop_topology } },
    variants: {},
    providers: {},
  } as unknown as Parameters<typeof ProfileView>[1]["doc"];
}

describe("ProfileView.svelte — e-stop wiring (#574)", () => {
  it("says what a power_cut e-stop does", () => {
    render(ProfileView, { props: { doc: profileDoc("power_cut") } });
    expect(screen.getByText("E-stop wiring")).toBeInTheDocument();
    expect(screen.getByText(/Cuts power to the actuator boards/)).toBeInTheDocument();
  });

  it("says what a signal e-stop does", () => {
    render(ProfileView, { props: { doc: profileDoc("signal") } });
    expect(screen.getByText(/Drives the boards' e-stop inputs/)).toBeInTheDocument();
  });
});

describe("Commission.svelte — runtime uptime (#574)", () => {
  it("shows uptime in hours and minutes, not raw seconds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === "/api/status")
          return jsonResponse(200, { running: true, active_project: "demo" });
        if (path === "/v0/runtime/status")
          return jsonResponse(200, {
            status: { code: "OK" },
            mode: "MANUAL",
            uptime_seconds: 3725,
            polling_interval_ms: 1000,
            device_count: 0,
            providers: [],
          });
        if (path === "/v0/providers/health") return jsonResponse(200, { providers: [] });
        return jsonResponse(200, {});
      }),
    );
    render(Commission, {
      props: {
        projectName: "demo",
        system: createProjectDocument("demo"),
        runtimeStatus: createRuntimeStatus({ running: true, active_project: "demo" }),
        commissionRunningForCurrent: true,
      },
    });

    expect(await screen.findByText("Uptime: 1 h 2 min")).toBeInTheDocument();
    expect(screen.queryByText(/3725s/)).toBeNull();
  });
});
