import { afterEach, describe, expect, it } from "vitest";

import { noteWorkbenchInstance, resetWorkbenchInstance } from "../../src/lib/workbench-instance";

const status = (instance?: string, package_version?: string | null) => ({
  active_project: "demo",
  running: true,
  ...(instance === undefined ? {} : { workbench: { version: 1, instance, package_version } }),
});

afterEach(() => resetWorkbenchInstance());

describe("noteWorkbenchInstance (#777)", () => {
  it("records the first instance and reports a later different one with its version", () => {
    expect(noteWorkbenchInstance(status("a", "0.30.0"))).toBeNull();
    expect(noteWorkbenchInstance(status("a", "0.30.0"))).toBeNull();
    expect(noteWorkbenchInstance(status("b", "0.31.0"))).toBe("0.31.0");
  });

  it("compares with the first instance seen, not the latest", () => {
    noteWorkbenchInstance(status("a"));
    noteWorkbenchInstance(status("b", "0.31.0"));
    expect(noteWorkbenchInstance(status("b", "0.31.0"))).toBe("0.31.0");
  });

  it("reports an unnamed version as empty, and ignores a status without an instance", () => {
    expect(noteWorkbenchInstance(status())).toBeNull();
    expect(noteWorkbenchInstance(status("a"))).toBeNull();
    expect(noteWorkbenchInstance(status())).toBeNull();
    expect(noteWorkbenchInstance(status("b", null))).toBe("");
  });
});
