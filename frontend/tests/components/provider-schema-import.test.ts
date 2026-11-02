import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProviderSchemasResponse } from "../../src/lib/contracts";
import ProviderSchemaImport from "../../src/lib/ProviderSchemaImport.svelte";
import Compose from "../../src/routes/Compose.svelte";
import { createProjectDocument, createRuntimeStatus, jsonResponse, pathFromInput, withProvider } from "./helpers";
import { reactive } from "./helpers.svelte";

const HERE = dirname(fileURLToPath(import.meta.url));

function loadEnvelope(kind: string) {
  return JSON.parse(
    readFileSync(resolve(HERE, `../../../anolis_workbench/schemas/providers/${kind}.config-schema.json`), "utf-8"),
  );
}

const IMPORT_REPLY = {
  kind: "foo",
  version: "0.1.0",
  status: "imported",
  sha256: "cd".repeat(32),
  repo: "anolishq/anolis-provider-foo",
};

function envelopeFile(): File {
  return new File(['{"provider": "anolis-provider-foo"}'], "foo-0.1.0.json", { type: "application/json" });
}

function chooseFile(file: File): Promise<boolean> {
  const input = screen.getByLabelText(/Import a provider schema/) as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  return fireEvent.change(input);
}

function urlFrom(input: RequestInfo | URL): URL {
  const raw = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  return new URL(raw, "http://localhost");
}

function kindValues(select: HTMLElement): string[] {
  return Array.from((select as HTMLSelectElement).options).map((o) => o.value);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProviderSchemaImport (#787)", () => {
  it("posts the file's bytes to the import route and reports the import", async () => {
    const calls: { url: URL; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: urlFrom(input), init });
        return jsonResponse(200, IMPORT_REPLY);
      }),
    );
    const onImported = vi.fn();
    render(ProviderSchemaImport, { props: { onImported } });

    const file = envelopeFile();
    await chooseFile(file);

    expect(await screen.findByTestId("provider-import-result")).toHaveTextContent(
      "foo 0.1.0: imported (sha256 cdcdcdcdcdcd, deploys from anolishq/anolis-provider-foo)",
    );
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(1);
    const url = calls[0]!.url;
    expect(url.pathname).toBe("/api/provider-schemas/import");
    expect(Object.fromEntries(url.searchParams)).toEqual({ filename: "foo-0.1.0.json" });
    expect(calls[0]!.init?.method).toBe("POST");
    expect(calls[0]!.init?.body).toBe(file);
  });

  it("passes replace and repo when they are set", async () => {
    const urls: URL[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(urlFrom(input));
        return jsonResponse(200, { ...IMPORT_REPLY, status: "replaced", repo: "example/anolis-provider-foo" });
      }),
    );
    render(ProviderSchemaImport, { props: { onImported: vi.fn() } });

    await fireEvent.click(screen.getByLabelText(/Replace an import of the same version/));
    await fireEvent.input(screen.getByLabelText(/Deploys from/), {
      target: { value: " example/anolis-provider-foo " },
    });
    await chooseFile(envelopeFile());

    await screen.findByTestId("provider-import-result");
    const url = urls[0]!;
    expect(url.searchParams.get("replace")).toBe("1");
    expect(url.searchParams.get("repo")).toBe("example/anolis-provider-foo");
  });

  it("shows the server's refusal, and refreshes nothing", async () => {
    const reason = "foo 0.1.0 is already imported with other content; pass --replace to replace it";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(400, { error: reason })),
    );
    const onImported = vi.fn();
    render(ProviderSchemaImport, { props: { onImported } });

    await chooseFile(envelopeFile());

    expect(await screen.findByRole("alert")).toHaveTextContent(`Not imported: ${reason}`);
    expect(onImported).not.toHaveBeenCalled();
    expect(screen.queryByTestId("provider-import-result")).not.toBeInTheDocument();
  });
});

describe("Compose offers an imported kind at once (#787)", () => {
  it("asks for the schemas again after an import, and the kind can be added", async () => {
    let imported = false;
    const resolves: number[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path.startsWith("/api/provider-schemas/import")) {
          imported = true;
          return jsonResponse(200, IMPORT_REPLY);
        }
        resolves.push(imported ? 1 : 0);
        const reply: ProviderSchemasResponse = {
          schema_version: 1,
          providers: { sim: loadEnvelope("sim") },
        };
        if (imported) {
          const foo = loadEnvelope("sim");
          foo.provider = "anolis-provider-foo";
          foo.provider_version = "0.1.0";
          reply.providers.foo = foo;
        }
        return jsonResponse(200, reply);
      }),
    );
    const doc = createProjectDocument("demo");
    withProvider(doc, "sim0", "sim", {});
    render(Compose, {
      props: {
        projectName: "demo",
        system: reactive(doc),
        providerSchemas: { schema_version: 1, providers: { sim: loadEnvelope("sim") } },
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    const kindSelect = screen.getByLabelText("Kind for the new provider");
    await waitFor(() => expect(resolves).toEqual([0]));
    expect(kindValues(kindSelect)).not.toContain("foo");

    await chooseFile(envelopeFile());

    await waitFor(() => expect(kindValues(kindSelect)).toContain("foo"));
    expect(resolves).toEqual([0, 1]);
  });
});
