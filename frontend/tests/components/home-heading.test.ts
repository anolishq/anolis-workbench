import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Home from "../../src/routes/Home.svelte";
import type { ProjectSummary } from "../../src/lib/contracts";
import { createImportedDocument, jsonResponse, pathFromInput } from "./helpers";

// wb#730: with an installed project, Home's heading is that machine, with a way
// back to its Operate; with none, it is a neutral "Projects".

const INSTALLED: ProjectSummary = {
  name: "bioreactor-v1",
  authored: false,
  meta: { installed_from: "/opt/anolis" },
};
const AUTHORED: ProjectSummary = { name: "alpha", authored: true, meta: {} };

function stubProject(status: number, displayName?: string) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const path = pathFromInput(input);
    if (path === "/api/projects/bioreactor-v1") {
      const doc = createImportedDocument("bioreactor-v1");
      if (displayName) doc.profile = { ...doc.profile, display_name: displayName };
      return Promise.resolve(jsonResponse(status, status === 200 ? doc : { error: "gone" }));
    }
    return Promise.resolve(jsonResponse(200, {}));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderHome(projects: ProjectSummary[]) {
  const onNavigate = vi.fn();
  render(Home, {
    props: { projects, templates: [], onNavigate, onProjectsRefreshed: vi.fn() },
  });
  return onNavigate;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Home.svelte heading (#730)", () => {
  it("is the installed machine, by its display name, with a link to its Operate", async () => {
    stubProject(200, "Bioreactor v1 (rig)");
    const onNavigate = renderHome([AUTHORED, INSTALLED]);

    const heading = screen.getByRole("heading", { level: 1 });
    await waitFor(() => expect(heading).toHaveTextContent("Bioreactor v1 (rig)"));
    expect(heading).toHaveTextContent("this machine");
    expect(screen.queryByText(/Commissioning/)).toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: "Go to Operate" }));
    expect(onNavigate).toHaveBeenCalledWith("/projects/bioreactor-v1/operate");
    // The project list and Setup stay below it.
    expect(screen.getByRole("heading", { name: "Existing Projects" })).toBeInTheDocument();
    expect(screen.getByTestId("home-setup")).toBeInTheDocument();
  });

  it("names the installed machine by its project name when its profile can't be read", async () => {
    const fetchMock = stubProject(404);
    renderHome([INSTALLED]);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([i]) => pathFromInput(i) === "/api/projects/bioreactor-v1"),
      ).toBe(true),
    );
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("bioreactor-v1");
    expect(heading).toHaveTextContent("this machine");
  });

  it('is a neutral "Projects" with no installed project', () => {
    const fetchMock = stubProject(200);
    renderHome([AUTHORED]);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/^Projects$/);
    expect(screen.queryByText(/Commissioning/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Go to Operate" })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
