import { afterEach, describe, expect, it } from "vitest";

import {
  describeWorkbenchChange,
  noteWorkbenchInstance,
  resetWorkbenchInstance,
} from "../../src/lib/workbench-instance";

const status = (instance?: string, package_version?: string | null) => ({
  active_project: "demo",
  running: true,
  ...(instance === undefined ? {} : { workbench: { version: 1, instance, package_version } }),
});

afterEach(() => resetWorkbenchInstance());

describe("noteWorkbenchInstance (#777)", () => {
  it("records the first instance and reports a later different one with both versions", () => {
    expect(noteWorkbenchInstance(status("a", "0.30.0"))).toBeNull();
    expect(noteWorkbenchInstance(status("a", "0.30.0"))).toBeNull();
    expect(noteWorkbenchInstance(status("b", "0.31.0"))).toEqual({ from: "0.30.0", to: "0.31.0" });
  });

  it("compares with the first instance seen, not the latest", () => {
    noteWorkbenchInstance(status("a", "0.30.0"));
    noteWorkbenchInstance(status("b", "0.31.0"));
    expect(noteWorkbenchInstance(status("b", "0.31.0"))).toEqual({ from: "0.30.0", to: "0.31.0" });
  });

  it("ignores a status without an instance, and reports an unnamed version as empty", () => {
    expect(noteWorkbenchInstance(status())).toBeNull();
    expect(noteWorkbenchInstance(status("a"))).toBeNull();
    expect(noteWorkbenchInstance(status())).toBeNull();
    expect(noteWorkbenchInstance(status("b", null))).toEqual({ from: "", to: "" });
  });
});

describe("describeWorkbenchChange (#777)", () => {
  it("names both versions for an upgrade", () => {
    expect(describeWorkbenchChange({ from: "0.30.0", to: "0.31.0" })).toBe("updated from 0.30.0 to 0.31.0");
  });

  it("says restarted when the version is the same or unknown", () => {
    expect(describeWorkbenchChange({ from: "0.30.0", to: "0.30.0" })).toBe("restarted");
    expect(describeWorkbenchChange({ from: "", to: "0.31.0" })).toBe("restarted");
  });
});
