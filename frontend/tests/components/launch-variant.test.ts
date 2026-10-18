import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import App from "../../src/App.svelte";
import Commission from "../../src/routes/Commission.svelte";
import Operate from "../../src/routes/Operate.svelte";
import { AUTOMATION_VARIANT, variantRelpath } from "../../src/lib/canonical";
import type { ProjectDocument } from "../../src/lib/contracts";
import { createProjectDocument, createRuntimeStatus, jsonResponse, pathFromInput } from "./helpers";
import { reactive } from "./helpers.svelte";

const BANNER = "Editing automation; Launch runs manual";

/** demo with both variants. `launch` undefined means an old project that never wrote it. */
function twoVariantDoc(launch?: string): ProjectDocument {
  const doc = createProjectDocument("demo");
  doc.variants[AUTOMATION_VARIANT] = {
    ...structuredClone(doc.variants.manual),
    automation: { enabled: true },
  };
  doc.profile.runtime_profiles[AUTOMATION_VARIANT] = variantRelpath(AUTOMATION_VARIANT);
  if (launch === undefined) delete doc.launch;
  else doc.launch = { variant: launch };
  return doc;
}

function launchSelect(): HTMLSelectElement {
  return screen.getByRole("combobox", { name: "Dev-launch variant" }) as HTMLSelectElement;
}

function putBodies(fetchMock: ReturnType<typeof vi.fn>): ProjectDocument[] {
  return fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "PUT")
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)) as ProjectDocument);
}

const PASSING_PREFLIGHT = {
  ok: true,
  checks: [{ name: "Runtime binary exists", ok: true, error: null, hint: null }],
  summary: { passed: 1, skipped: 0, failed: 0 },
};

/** Serve `saved` as the project on disk; record every request. */
function serveSaved(saved: ProjectDocument) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = pathFromInput(input);
    if (path === "/api/projects/demo" && (init?.method ?? "GET") === "GET")
      return jsonResponse(200, structuredClone(saved));
    return jsonResponse(200, {});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The control is enabled once the saved project has loaded. */
async function loadedLaunchSelect(): Promise<HTMLSelectElement> {
  await waitFor(() => expect(launchSelect()).toBeEnabled());
  return launchSelect();
}

function renderCommission(system: ProjectDocument, editingVariant = "") {
  return render(Commission, {
    props: {
      projectName: "demo",
      system,
      runtimeStatus: createRuntimeStatus(),
      commissionRunningForCurrent: false,
      editingVariant,
    },
  });
}

describe("Commission: the dev-launch variant control (#339)", () => {
  it("shows launch.variant", async () => {
    serveSaved(twoVariantDoc(AUTOMATION_VARIANT));
    renderCommission(twoVariantDoc(AUTOMATION_VARIANT));
    expect((await loadedLaunchSelect()).value).toBe(AUTOMATION_VARIANT);
  });

  it("shows manual for a project that never wrote launch.variant", async () => {
    const doc = twoVariantDoc();
    // Key order would make "the first variant" automation.
    doc.variants = { automation: doc.variants.automation, manual: doc.variants.manual };
    serveSaved(doc);
    renderCommission(structuredClone(doc));
    expect((await loadedLaunchSelect()).value).toBe("manual");
  });

  it("does not offer a variant that exists only in unsaved edits", async () => {
    // automation was added in Compose and not saved yet.
    const fetchMock = serveSaved(createProjectDocument("demo"));
    const system = reactive(twoVariantDoc("manual"));
    renderCommission(system);

    const select = await loadedLaunchSelect();
    const option = select.querySelector(`option[value="${AUTOMATION_VARIANT}"]`);
    expect(option === null || (option as HTMLOptionElement).disabled).toBe(true);
    if (option) expect(option.textContent).toMatch(/save/i);

    await fireEvent.change(select, { target: { value: AUTOMATION_VARIANT } });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(putBodies(fetchMock)).toHaveLength(0);
    expect(system.launch?.variant).toBe("manual");
    expect(launchSelect().value).toBe("manual");
  });

  it.each([
    ["a variant only in unsaved edits", AUTOMATION_VARIANT],
    ["a variant that exists nowhere", "gone"],
  ])("shows manual when launch.variant names %s, and does not rewrite it", async (_what, named) => {
    const saved = createProjectDocument("demo");
    saved.launch = { variant: named };
    const fetchMock = serveSaved(saved);
    const system = reactive(twoVariantDoc(named));
    renderCommission(system);

    expect((await loadedLaunchSelect()).value).toBe("manual");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(putBodies(fetchMock)).toHaveLength(0);
    expect(system.launch?.variant).toBe(named);
  });

  it("writes launch.variant into the saved project, and nothing else from memory", async () => {
    const saved = twoVariantDoc("manual");
    const fetchMock = serveSaved(saved);

    // An unsaved Compose edit is in memory; choosing the launch variant must
    // not save it behind the user's back.
    const system = reactive(twoVariantDoc("manual"));
    system.variants.manual.logging = { level: "debug" };
    renderCommission(system);

    await fireEvent.change(await loadedLaunchSelect(), { target: { value: AUTOMATION_VARIANT } });

    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    const [body] = putBodies(fetchMock);
    expect(body.launch?.variant).toBe(AUTOMATION_VARIANT);
    expect(body.variants.manual.logging).toEqual({ level: "info" });
    await waitFor(() => expect(system.launch?.variant).toBe(AUTOMATION_VARIANT));
    expect(launchSelect().value).toBe(AUTOMATION_VARIANT);
  });
});

describe("the edited/launched mismatch banner (#339)", () => {
  it.each([
    ["Commission", (doc: ProjectDocument, editing: string) => renderCommission(doc, editing)],
    [
      "Operate",
      (doc: ProjectDocument, editing: string) =>
        render(Operate, {
          props: {
            projectName: "demo",
            system: doc,
            runtimeStatus: createRuntimeStatus(),
            editingVariant: editing,
          },
        }),
    ],
  ])("%s shows it only on a mismatch", async (_name, renderView) => {
    const cases: Array<[string | undefined, string, boolean]> = [
      ["manual", AUTOMATION_VARIANT, true],
      [undefined, AUTOMATION_VARIANT, true],
      ["manual", "manual", false],
      [undefined, "", false],
      [AUTOMATION_VARIANT, AUTOMATION_VARIANT, false],
    ];
    for (const [launch, editing, shown] of cases) {
      serveSaved(twoVariantDoc(launch));
      const { unmount } = renderView(twoVariantDoc(launch), editing);
      if (_name === "Commission") await loadedLaunchSelect();
      const banner = screen.queryByTestId("launch-variant-mismatch");
      if (shown) expect(banner, `${launch} / ${editing}`).toHaveTextContent(BANNER);
      else expect(banner, `${launch} / ${editing}`).toBeNull();
      unmount();
    }
  });

  it("does not block Launch or ask for confirmation", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === "/api/projects/demo") return jsonResponse(200, twoVariantDoc("manual"));
      if (path === "/api/status") return jsonResponse(200, { running: false });
      if (path === "/api/projects/demo/preflight") return jsonResponse(200, PASSING_PREFLIGHT);
      return jsonResponse(200, { ok: true });
    });
    vi.stubGlobal("fetch", fetchMock);
    const confirmSpy = vi.fn(() => false);
    vi.stubGlobal("confirm", confirmSpy);

    renderCommission(twoVariantDoc("manual"), AUTOMATION_VARIANT);
    expect(screen.getByTestId("launch-variant-mismatch")).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Preflight Check" }));
    await screen.findByText("Runtime binary exists");
    expect(screen.getByRole("button", { name: /Launch/ })).toBeEnabled();
    await fireEvent.click(screen.getByRole("button", { name: /Launch/ }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([input]) => pathFromInput(input) === "/api/projects/demo/launch",
        ),
      ).toBe(true);
    });
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});

describe("App: the Compose selector and the launch variant (#339)", () => {
  function stubApi(saved: ProjectDocument) {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (path === "/api/projects")
        return Promise.resolve(jsonResponse(200, [{ name: "demo", meta: {} }]));
      if (path === "/api/projects/demo" && (init?.method ?? "GET") === "GET")
        return Promise.resolve(jsonResponse(200, structuredClone(saved)));
      if (path === "/api/status")
        return Promise.resolve(jsonResponse(200, { running: false, active_project: null }));
      if (path === "/api/templates") return Promise.resolve(jsonResponse(200, []));
      if (path === "/api/provider-schemas")
        return Promise.resolve(jsonResponse(200, { providers: {} }));
      return Promise.resolve(jsonResponse(200, {}));
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it.each([["manual"], [undefined]])(
    "with launch.variant %s: choosing automation in Compose moves only the banner",
    async (launch) => {
      const fetchMock = stubApi(twoVariantDoc(launch));
      history.replaceState({}, "", "/projects/demo/compose");
      render(App);

      const select = (await screen.findByLabelText("Runtime variant")) as HTMLSelectElement;
      expect(select.value).toBe("manual");
      await fireEvent.change(select, { target: { value: AUTOMATION_VARIANT } });

      await fireEvent.click(screen.getByRole("button", { name: "Commission" }));
      expect(await screen.findByTestId("launch-variant-mismatch")).toHaveTextContent(BANNER);
      expect(launchSelect().value).toBe("manual");

      await fireEvent.click(screen.getByRole("button", { name: "Operate" }));
      expect(await screen.findByTestId("launch-variant-mismatch")).toHaveTextContent(BANNER);

      // Selecting a variant to look at it wrote nothing.
      expect(putBodies(fetchMock)).toHaveLength(0);

      // Pointing dev-launch at the edited variant clears the banner.
      await fireEvent.click(screen.getByRole("button", { name: "Commission" }));
      await fireEvent.change(await screen.findByRole("combobox", { name: "Dev-launch variant" }), {
        target: { value: AUTOMATION_VARIANT },
      });
      await waitFor(() => {
        expect(screen.queryByTestId("launch-variant-mismatch")).not.toBeInTheDocument();
      });
      expect(putBodies(fetchMock).map((b) => b.launch?.variant)).toEqual([AUTOMATION_VARIANT]);
      await fireEvent.click(screen.getByRole("button", { name: "Operate" }));
      await screen.findByRole("heading", { name: "Operate" });
      expect(screen.queryByTestId("launch-variant-mismatch")).not.toBeInTheDocument();
    },
  );
});
