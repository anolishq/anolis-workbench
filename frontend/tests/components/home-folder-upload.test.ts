import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Home from "../../src/routes/Home.svelte";
import { jsonResponse } from "./helpers";

// wb#749: from a browser on another computer, "Choose folder…" uploads a
// project folder to POST /api/projects/import-upload; the path field stays
// for a directory on the workbench's machine.

function file(relativePath: string, text: string): File {
  const f = new File([text], relativePath.split("/").pop() ?? "f");
  Object.defineProperty(f, "webkitRelativePath", { value: relativePath });
  return f;
}

function renderHome(answer: () => Response) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    if (String(input).includes("/api/projects/import-upload")) return answer();
    return jsonResponse(200, []);
  });
  vi.stubGlobal("fetch", fetchMock);
  const onNavigate = vi.fn();
  render(Home, {
    props: { projects: [], templates: [], onNavigate, onProjectsRefreshed: vi.fn() },
  });
  return { fetchMock, onNavigate };
}

async function choose(files: File[]) {
  const input = screen.getByTestId("import-folder-input") as HTMLInputElement;
  expect(input).toHaveAttribute("webkitdirectory");
  Object.defineProperty(input, "files", { value: files, configurable: true });
  await fireEvent.change(input);
}

afterEach(() => vi.unstubAllGlobals());

describe("Home.svelte: import a folder from this computer (wb#749)", () => {
  it("uploads the chosen folder, then opens the imported project", async () => {
    const { fetchMock, onNavigate } = renderHome(() =>
      jsonResponse(201, { name: "rig-a", format: "machine-profile", warnings: [] }),
    );
    expect(screen.getByRole("button", { name: "Choose folder…" })).toBeInTheDocument();

    await choose([
      file("rig-a/machine-profile.yaml", "schema_version: 1\n"),
      file("rig-a/config/x.yaml", "x"),
    ]);

    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith("/projects/rig-a/compose"));
    const call = fetchMock.mock.calls.find(([i]) => String(i).includes("/api/projects/import-upload"));
    expect(call?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      folder: "rig-a",
      files: [
        { path: "machine-profile.yaml", content: btoa("schema_version: 1\n") },
        { path: "config/x.yaml", content: btoa("x") },
      ],
    });
  });

  it("shows the server's reasons when the upload is refused", async () => {
    renderHome(() =>
      jsonResponse(400, {
        error: "Machine-profile directory failed import validation",
        code: "import_validation_failed",
        errors: [
          {
            source: "import",
            code: "import.validation",
            path: "$",
            message: "The upload has 250 files; the limit is 200",
          },
        ],
      }),
    );

    await choose([file("rig-a/machine-profile.yaml", "x")]);

    expect(await screen.findByText(/the limit is 200/)).toBeInTheDocument();
  });
});
