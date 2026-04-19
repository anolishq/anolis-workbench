import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadBlob, fetchJson, filenameFromContentDisposition } from "../../src/lib/api";

function mockFetch(response: { ok: boolean; status: number; body: string }) {
  const fetchMock = vi.fn(async () => ({
    ok: response.ok,
    status: response.status,
    text: async () => response.body,
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("filenameFromContentDisposition", () => {
  it("extracts UTF-8 encoded filename value", () => {
    const header = "attachment; filename*=UTF-8''project%20export.anpkg";
    expect(filenameFromContentDisposition(header, "fallback.anpkg")).toBe("project export.anpkg");
  });

  it("falls back to raw UTF-8 payload when decode fails", () => {
    const header = "attachment; filename*=UTF-8''project%2Xexport.anpkg";
    expect(filenameFromContentDisposition(header, "fallback.anpkg")).toBe("project%2Xexport.anpkg");
  });

  it("extracts simple filename attribute", () => {
    expect(
      filenameFromContentDisposition('attachment; filename="simple-name.anpkg"', "fallback.anpkg"),
    ).toBe("simple-name.anpkg");
  });

  it("returns fallback when header is absent or empty", () => {
    expect(filenameFromContentDisposition(null, "fallback.anpkg")).toBe("fallback.anpkg");
    expect(filenameFromContentDisposition("   ", "fallback.anpkg")).toBe("fallback.anpkg");
  });
});

describe("fetchJson", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns parsed JSON for successful responses", async () => {
    mockFetch({
      ok: true,
      status: 200,
      body: JSON.stringify({ ok: true, message: "ready" }),
    });
    await expect(fetchJson("/api/status")).resolves.toEqual({ ok: true, message: "ready" });
  });

  it("returns empty object for empty successful response body", async () => {
    mockFetch({ ok: true, status: 204, body: "" });
    await expect(fetchJson("/api/no-content")).resolves.toEqual({});
  });

  it("throws invalid JSON error when 2xx response body is not JSON", async () => {
    mockFetch({ ok: true, status: 200, body: "not-json" });
    await expect(fetchJson("/api/status")).rejects.toThrow("Invalid JSON from /api/status");
  });

  it("surfaces API error field for non-2xx JSON responses", async () => {
    mockFetch({
      ok: false,
      status: 400,
      body: JSON.stringify({ error: "validation_failed" }),
    });
    await expect(fetchJson("/api/projects/x")).rejects.toThrow("validation_failed");
  });

  it("throws raw HTTP+text when non-2xx response body is not JSON", async () => {
    mockFetch({ ok: false, status: 500, body: "plain failure" });
    await expect(fetchJson("/api/projects/x")).rejects.toThrow("HTTP 500: plain failure");
  });

  it("falls back to status code when non-2xx response body is empty", async () => {
    mockFetch({ ok: false, status: 503, body: "" });
    await expect(fetchJson("/api/projects/x")).rejects.toThrow("HTTP 503");
  });
});

describe("downloadBlob", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("creates an anchor, triggers download, and revokes object URL", () => {
    vi.useFakeTimers();

    const createObjectURL = vi.fn(() => "blob:unit-test");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });

    const click = vi.fn();
    const remove = vi.fn();
    const anchor = {
      href: "",
      download: "",
      click,
      remove,
    };
    const createElement = vi.fn(() => anchor);
    const appendChild = vi.fn();

    vi.stubGlobal("document", {
      createElement,
      body: { appendChild },
    });

    downloadBlob(new Blob(["hello"]), "sample.txt");

    expect(createElement).toHaveBeenCalledWith("a");
    expect(anchor.href).toBe("blob:unit-test");
    expect(anchor.download).toBe("sample.txt");
    expect(appendChild).toHaveBeenCalledWith(anchor);
    expect(click).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).not.toHaveBeenCalled();

    vi.runOnlyPendingTimers();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:unit-test");
  });
});
