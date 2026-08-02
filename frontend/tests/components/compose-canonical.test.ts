import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import { AUTOMATION_VARIANT, MANUAL_VARIANT } from "../../src/lib/canonical";
import ProfileForm from "../../src/lib/ProfileForm.svelte";
import RuntimeForm from "../../src/lib/RuntimeForm.svelte";
import Compose from "../../src/routes/Compose.svelte";
import { createProjectDocument, createRuntimeStatus, jsonResponse, withProvider } from "./helpers";
import { reactive } from "./helpers.svelte";

describe("RuntimeForm.svelte (canonical runtime config)", () => {
  it("edits the runtime config's own fields, not a shadow document", async () => {
    const doc = reactive(createProjectDocument("demo"));
    const onChanged = vi.fn();
    render(RuntimeForm, { props: { doc, onChanged } });

    await fireEvent.change(screen.getByDisplayValue("8080"), { target: { value: "9090" } });
    await fireEvent.input(screen.getByDisplayValue("127.0.0.1"), { target: { value: "0.0.0.0" } });

    const manual = doc.variants[MANUAL_VARIANT];
    expect(manual.http?.port).toBe(9090);
    expect(manual.http?.bind).toBe("0.0.0.0");
    expect(onChanged).toHaveBeenCalled();
  });

  it("writes the runtime binary to host_paths, never into the deployed config", async () => {
    const doc = reactive(createProjectDocument("demo"));
    render(RuntimeForm, { props: { doc, onChanged: vi.fn() } });

    const inputs = screen.getAllByRole("textbox");
    const exePath = inputs[inputs.length - 1] as HTMLInputElement;
    await fireEvent.input(exePath, { target: { value: "/home/me/build/anolis-runtime" } });

    expect(doc.host_paths?.runtime_executable).toBe("/home/me/build/anolis-runtime");
    // The runtime config carries no host paths — install.sh installs the pinned
    // binary under its own prefix.
    expect(JSON.stringify(doc.variants[MANUAL_VARIANT])).not.toContain("/home/me");
  });

  it("warns when the manual variant would boot into automation", () => {
    const doc = createProjectDocument("demo");
    doc.variants[MANUAL_VARIANT].automation = { enabled: true };

    render(RuntimeForm, { props: { doc: reactive(doc), onChanged: vi.fn() } });

    // install.sh refuses this outright; catching it here beats failing at sudo.
    expect(screen.getByTestId("inertness-warning")).toBeInTheDocument();
  });
});

describe("ProfileForm.svelte", () => {
  it("warns until every kind a variant runs is pinned", async () => {
    const doc = createProjectDocument("demo");
    withProvider(doc, "bread0", "bread", {});
    delete doc.profile.components!.providers!.bread;
    const reactiveDoc = reactive(doc);

    render(ProfileForm, { props: { doc: reactiveDoc, onChanged: vi.fn() } });

    expect(screen.getByTestId("missing-pins-warning")).toHaveTextContent("bread");

    await fireEvent.input(screen.getByLabelText("bread version"), { target: { value: "0.3.8" } });

    expect(reactiveDoc.profile.components?.providers?.bread).toEqual({
      repo: "anolishq/anolis-provider-bread",
      version: "0.3.8",
    });
    await waitFor(() => {
      expect(screen.queryByTestId("missing-pins-warning")).not.toBeInTheDocument();
    });
  });

  it("does not let the machine_id be edited", () => {
    const doc = reactive(createProjectDocument("demo"));
    render(ProfileForm, { props: { doc, onChanged: vi.fn() } });

    // It is the deploy identity: install.sh keys the installed directory and
    // every config path token on it.
    expect(screen.getByLabelText("Machine ID")).toHaveAttribute("readonly");
  });
});

describe("Compose.svelte (variants)", () => {
  it("adds automation as a SEPARATE variant, leaving manual inert", async () => {
    const doc = reactive(createProjectDocument("demo"));
    render(Compose, {
      props: {
        projectName: "demo",
        system: doc,
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    // The behaviour tree is authored outside the workbench, so the control asks
    // for its path rather than inventing one that isn't there.
    await fireEvent.click(screen.getByRole("button", { name: /Automation variant/ }));
    await fireEvent.input(screen.getByPlaceholderText("behaviors/main.xml"), {
      target: { value: "behaviors/main.xml" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Add" }));

    // Before #255 the composer set automation on its ONLY config, and install.sh
    // rejected every such deploy for having a non-inert manual variant.
    expect(doc.variants[AUTOMATION_VARIANT].automation).toEqual({
      enabled: true,
      behavior_tree: "../anolis-projects/projects/demo/behaviors/main.xml",
    });
    expect(doc.variants[MANUAL_VARIANT].automation).toEqual({ enabled: false });
    expect(doc.profile.runtime_profiles[AUTOMATION_VARIANT]).toBe(
      "config/anolis-runtime.automation.yaml",
    );
    expect(doc.profile.behaviors).toEqual(["behaviors/main.xml"]);
  });

  it("sends the whole document on save, so unknown keys survive", async () => {
    const doc = createProjectDocument("demo");
    // A key the workbench has no model for must round-trip untouched.
    doc.variants[MANUAL_VARIANT].experimental_knob = { deep: true };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(200, { ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(Compose, {
      props: {
        projectName: "demo",
        system: reactive(doc),
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.variants.manual.experimental_knob).toEqual({ deep: true });
    expect(body.profile.machine_id).toBe("demo");
  });
});

describe("Compose.svelte (automation variant is reversible)", () => {
  it("removes the variant and its behaviour reference again", async () => {
    const doc = reactive(createProjectDocument("demo"));
    render(Compose, {
      props: {
        projectName: "demo",
        system: doc,
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    await fireEvent.click(screen.getByRole("button", { name: /Automation variant/ }));
    await fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(doc.variants[AUTOMATION_VARIANT]).toBeDefined();

    await fireEvent.click(screen.getByRole("button", { name: /Remove automation variant/ }));

    // A dangling behaviours reference blocks EVERY deploy of the project, so
    // undoing has to take the profile entry with it.
    expect(doc.variants[AUTOMATION_VARIANT]).toBeUndefined();
    expect(doc.profile.runtime_profiles[AUTOMATION_VARIANT]).toBeUndefined();
    expect(doc.profile.behaviors).toBeUndefined();
  });
});
