import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import type { ImportedProjectDoc } from "../../src/lib/contracts";
import ProfileView from "../../src/lib/ProfileView.svelte";
import Commission from "../../src/routes/Commission.svelte";
import Compose from "../../src/routes/Compose.svelte";
import Home from "../../src/routes/Home.svelte";
import { createRuntimeStatus, jsonResponse } from "./helpers";

function importedDoc(): ImportedProjectDoc {
  return {
    format: "machine-profile",
    meta: {
      name: "rig-a",
      imported_from: "/repos/anolis-projects/projects/bioreactor-v1",
      profile_dir_name: "bioreactor-v1",
    },
    profile: {
      schema_version: 1,
      machine_id: "bioreactor-v1",
      display_name: "Bioreactor Lab Profile v1",
      runtime_profiles: {
        manual: "config/anolis-runtime.bioreactor.manual.yaml",
        automation: "config/anolis-runtime.bioreactor.automation.yaml",
      },
      providers: {
        bread0: { config: "config/provider-bread.bioreactor.yaml", notes: "CRUMBS bus devices" },
      },
      behaviors: ["behaviors/bioreactor_stir_dual_dosing.xml"],
      safety: { estop_topology: "power_cut" },
      components: {
        runtime: { repo: "anolishq/anolis", version: "0.1.39" },
        providers: { bread: { repo: "anolishq/anolis-provider-bread", version: "0.3.8" } },
      },
    },
    warnings: ["Provider 'bread0' (kind 'bread'): sample warning"],
  };
}

describe("ProfileView.svelte", () => {
  it("renders the verbatim banner, variants, safety block and warnings", () => {
    render(ProfileView, { props: { doc: importedDoc() } });

    expect(screen.getByText(/carried/)).toBeInTheDocument();
    expect(screen.getByText("bioreactor-v1")).toBeInTheDocument();
    expect(screen.getByText("manual")).toBeInTheDocument();
    expect(screen.getByText("automation")).toBeInTheDocument();
    expect(screen.getByText("power_cut")).toBeInTheDocument();
    expect(screen.getByText(/sample warning/)).toBeInTheDocument();
    expect(screen.getByText(/v0.1.39/)).toBeInTheDocument();
  });
});

describe("Compose.svelte (imported project)", () => {
  it("shows the read-only profile view and no Save button", () => {
    render(Compose, {
      props: {
        projectName: "rig-a",
        system: importedDoc(),
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    expect(screen.getByText(/Edit it in its source repository/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
  });
});

describe("Commission.svelte (imported project)", () => {
  it("hides launch/preflight/.anpkg and offers the variant selector", () => {
    render(Commission, {
      props: {
        projectName: "rig-a",
        system: importedDoc(),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    expect(screen.queryByRole("button", { name: /Preflight/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Launch/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Export \.anpkg/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Export Bundle/ })).toBeInTheDocument();

    const variantSelect = screen.getByRole("combobox") as HTMLSelectElement;
    expect(variantSelect.value).toBe("manual"); // prefers manual
    expect(screen.getByRole("option", { name: "automation" })).toBeInTheDocument();
  });
});

describe("Home.svelte import card", () => {
  it("imports via the API, surfaces warnings, and navigates", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/projects/import")) {
        return jsonResponse(201, {
          name: "rig-a",
          format: "machine-profile",
          warnings: ["a warning"],
        });
      }
      return jsonResponse(200, []);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onNavigate = vi.fn();

    render(Home, {
      props: {
        projects: [],
        templates: [],
        onNavigate,
        onProjectsRefreshed: vi.fn(),
      },
    });

    await fireEvent.input(screen.getByLabelText("Profile directory"), {
      target: { value: "/some/profile-dir" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /Import Project/ }));

    await waitFor(() => {
      expect(onNavigate).toHaveBeenCalledWith("/projects/rig-a/compose");
    });
    const importCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/api/projects/import"),
    );
    expect(JSON.parse(String(importCall?.[1]?.body))).toEqual({ path: "/some/profile-dir" });
  });

  it("tags imported projects in the project list", () => {
    render(Home, {
      props: {
        projects: [
          { name: "composer-one", format: "system", meta: {} },
          { name: "rig-a", format: "machine-profile", meta: {} },
        ],
        templates: [],
        onNavigate: vi.fn(),
        onProjectsRefreshed: vi.fn(),
      },
    });

    expect(screen.getByTitle(/Imported machine profile/)).toBeInTheDocument();
  });
});
