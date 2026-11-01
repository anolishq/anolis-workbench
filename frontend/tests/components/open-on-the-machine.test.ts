import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../../src/App.svelte";
import Compose from "../../src/routes/Compose.svelte";
import Home from "../../src/routes/Home.svelte";
import type { ProjectSummary } from "../../src/lib/contracts";
import {
  createImportedDocument,
  createProjectDocument,
  createRuntimeStatus,
  jsonResponse,
  pathFromInput,
} from "./helpers";

// wb#730: a workbench with an installed project (#724) opens on that machine's
// Operate. Home lists the installed project first as "this machine", and keeps
// creating, importing, updates, rollback and fleet under "Setup". Open on an
// imported project goes to Operate.

const INSTALLED: ProjectSummary = {
  name: "bioreactor-v1",
  format: "machine-profile",
  authored: false,
  meta: { installed_from: "/opt/anolis" },
};
const AUTHORED: ProjectSummary = { name: "alpha", authored: true, meta: {} };
const IMPORTED: ProjectSummary = {
  name: "sim-quickstart",
  authored: false,
  meta: { imported_from: "/srv/sim" },
};

function stubApi(projects: ProjectSummary[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === "/api/projects") return Promise.resolve(jsonResponse(200, projects));
      const project = path.match(/^\/api\/projects\/([^/]+)$/);
      if (project) {
        const summary = projects.find((p) => p.name === project[1]);
        const doc =
          summary?.authored === false
            ? createImportedDocument(project[1])
            : createProjectDocument(project[1]);
        if (summary) doc.meta = { ...summary.meta };
        return Promise.resolve(jsonResponse(200, doc));
      }
      if (path === "/api/status")
        return Promise.resolve(jsonResponse(200, { running: false, active_project: null }));
      if (path === "/api/templates") return Promise.resolve(jsonResponse(200, []));
      if (path === "/api/onboarding")
        return Promise.resolve(jsonResponse(200, { first_run: false }));
      if (path === "/api/provider-schemas")
        return Promise.resolve(jsonResponse(200, { providers: {} }));
      return Promise.resolve(jsonResponse(200, {}));
    }),
  );
}

function renderHome(projects: ProjectSummary[], onNavigate = vi.fn()) {
  stubApi(projects);
  render(Home, {
    props: { projects, templates: [], onNavigate, onProjectsRefreshed: vi.fn() },
  });
  return onNavigate;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("App: open on the machine (#730)", () => {
  it("goes straight to the installed project's Operate", async () => {
    stubApi([AUTHORED, INSTALLED]);
    history.replaceState({}, "", "/");
    render(App);

    await waitFor(() => expect(window.location.pathname).toBe("/projects/bioreactor-v1/operate"));
    expect(await screen.findByRole("heading", { name: "Operate" })).toBeInTheDocument();
  });

  // wb#754: the first load used to replace the URL with the bare route,
  // dropping any query string a page reads on load.
  it("keeps a page's query string on the first load", async () => {
    stubApi([AUTHORED, INSTALLED]);
    history.replaceState({}, "", "/projects/bioreactor-v1/operate?connection=a");
    render(App);

    expect(await screen.findByRole("heading", { name: "Operate" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/projects/bioreactor-v1/operate");
    expect(window.location.search).toBe("?connection=a");
  });

  it("stays on Home when no project is installed", async () => {
    stubApi([AUTHORED, IMPORTED]);
    history.replaceState({}, "", "/");
    render(App);

    expect(await screen.findByText("sim-quickstart")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });

  it("shows Home, not the machine again, from the Home button", async () => {
    stubApi([AUTHORED, INSTALLED]);
    history.replaceState({}, "", "/");
    render(App);
    await waitFor(() => expect(window.location.pathname).toBe("/projects/bioreactor-v1/operate"));

    await fireEvent.click(document.querySelector("#btn-home") as HTMLElement);

    await waitFor(() => expect(window.location.pathname).toBe("/"));
    expect(await screen.findByTestId("home-setup")).toBeInTheDocument();
  });

  it("keeps a direct link to another page", async () => {
    stubApi([AUTHORED, INSTALLED]);
    history.replaceState({}, "", "/projects/alpha/compose");
    render(App);

    expect(await screen.findByRole("heading", { name: "Compose" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/projects/alpha/compose");
  });
});

describe("Home.svelte: the installed project and Setup (#730)", () => {
  it("lists the installed project first, as this machine, not imported", () => {
    renderHome([AUTHORED, IMPORTED, INSTALLED]);

    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("bioreactor-v1");
    expect(within(items[0]).getByText("this machine")).toBeInTheDocument();
    expect(within(items[0]).queryByText("imported")).toBeNull();
    const imported = items.find((li) => li.textContent?.includes("sim-quickstart"));
    expect(within(imported!).getByText("imported")).toBeInTheDocument();
  });

  it("puts Create, Import, updates, rollback and fleet under a closed Setup", () => {
    renderHome([AUTHORED, INSTALLED]);

    const setup = screen.getByTestId("home-setup") as HTMLDetailsElement;
    expect(setup.tagName).toBe("DETAILS");
    expect(setup.open).toBe(false);
    for (const name of [
      "Create Project",
      "Import Project",
      "Check for Updates",
      "Rollback",
      "Fleet Dashboard",
    ]) {
      expect(setup.contains(screen.getByRole("button", { name, hidden: true }))).toBe(true);
    }
    expect(setup.contains(screen.getByRole("heading", { name: "Existing Projects" }))).toBe(false);
  });

  it("leaves Setup open on a workbench with no installed project", () => {
    renderHome([AUTHORED]);

    expect((screen.getByTestId("home-setup") as HTMLDetailsElement).open).toBe(true);
  });

  it("opens an imported project on Operate and an authored one on Compose", async () => {
    const onNavigate = renderHome([AUTHORED, IMPORTED, INSTALLED]);

    const open = (name: string) => {
      const li = screen.getAllByRole("listitem").find((el) => el.textContent?.includes(name));
      return within(li!).getByRole("button", { name: "Open" });
    };
    await fireEvent.click(open("bioreactor-v1"));
    await fireEvent.click(open("sim-quickstart"));
    await fireEvent.click(open("alpha"));

    expect(onNavigate.mock.calls.map(([path]) => path)).toEqual([
      "/projects/bioreactor-v1/operate",
      "/projects/sim-quickstart/operate",
      "/projects/alpha/compose",
    ]);
  });
});

describe("Compose.svelte: the running-runtime advisory (#730)", () => {
  function renderCompose(doc: ReturnType<typeof createProjectDocument>) {
    render(Compose, {
      props: {
        projectName: doc.meta?.name as string,
        system: doc,
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus({
          running: true,
          active_project: doc.meta?.name as string,
        }),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });
  }

  it("does not tell an imported project to save edits it cannot make", () => {
    renderCompose(createImportedDocument("sim-quickstart"));

    expect(screen.getByText(/Runtime is currently running from this project/)).toBeInTheDocument();
    expect(screen.queryByText(/Save edits now/)).toBeNull();
  });

  it("says the runtime runs the installed copy, for the installed project", () => {
    const doc = createImportedDocument("bioreactor-v1");
    doc.meta = { name: "bioreactor-v1", installed_from: "/opt/anolis" };
    renderCompose(doc);

    expect(screen.getByTestId("compose-runtime-advisory")).toHaveTextContent(
      "The runtime runs the installed copy of this project, in /opt/anolis.",
    );
    expect(screen.queryByText(/running from this project/)).toBeNull();
    expect(screen.queryByText(/Save edits now/)).toBeNull();
  });

  it("keeps the save-and-relaunch advice for an authored project", () => {
    renderCompose(createProjectDocument("alpha"));

    expect(screen.getByText(/Save edits now/)).toBeInTheDocument();
  });
});
