import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import Commission from "../../src/routes/Commission.svelte";
import { createProjectDocument, createRuntimeStatus, jsonResponse, pathFromInput } from "./helpers";

// Launch, Deploy and Restart act on the saved project. While Compose holds
// unsaved edits, each says so and offers the way back to Compose (#599).

function stubFetch(running: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      pathFromInput(input) === "/api/status"
        ? jsonResponse(200, { running, active_project: running ? "demo" : null })
        : jsonResponse(200, {}),
    ),
  );
}

function renderCommission(running: boolean, unsaved: boolean, onOpenCompose = vi.fn()) {
  stubFetch(running);
  render(Commission, {
    props: {
      projectName: "demo",
      system: createProjectDocument("demo"),
      runtimeStatus: createRuntimeStatus({
        running,
        active_project: running ? "demo" : null,
      }),
      commissionRunningForCurrent: running,
      unsaved,
      onOpenCompose,
    },
  });
  return onOpenCompose;
}

const notice = (where: string) => screen.queryByTestId(`unsaved-notice-${where}`);

describe("Commission.svelte — unsaved Compose edits (#599)", () => {
  it("says at Launch and Deploy that unsaved edits are left out", async () => {
    const onOpenCompose = renderCommission(false, true);
    await screen.findByRole("button", { name: "Launch →" });

    expect(notice("launch")).toHaveTextContent("unsaved Compose edits are not included");
    expect(notice("deploy")).toHaveTextContent("unsaved Compose edits are not included");

    const launchNotice = notice("launch") as HTMLElement;
    await fireEvent.click(launchNotice.querySelector("button") as HTMLButtonElement);
    expect(onOpenCompose).toHaveBeenCalledTimes(1);
  });

  it("says at Restart that unsaved edits are left out", async () => {
    renderCommission(true, true);
    await screen.findByRole("button", { name: "Restart" });

    expect(notice("restart")).toHaveTextContent("unsaved Compose edits are not included");
    expect(notice("deploy")).toBeInTheDocument();
  });

  it("says nothing about unsaved edits when there are none", async () => {
    renderCommission(false, false);
    await screen.findByRole("button", { name: "Launch →" });
    expect(notice("launch")).toBeNull();
    expect(notice("deploy")).toBeNull();
    expect(screen.queryByText(/unsaved edits are not included/)).toBeNull();
  });

  it("says nothing at Restart when there are no unsaved edits", async () => {
    renderCommission(true, false);
    await screen.findByRole("button", { name: "Restart" });
    expect(notice("restart")).toBeNull();
  });
});
