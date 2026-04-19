import { describe, expect, it } from "vitest";

import {
  coerceParameterValue,
  deriveOperateAvailability,
  extractAutomationStatus,
  extractAutomationTree,
  extractCapabilities,
  extractDeviceStateValues,
  extractDevices,
  extractMode,
  extractParameters,
  extractProvidersHealth,
  extractRuntimeStatus,
  normalizeArgSpec,
  normalizeFunctionArgs,
  normalizeFunctionSpecs,
  normalizeParameterType,
  normalizeProviderHealthQuality,
  renderBtOutline,
} from "../../src/lib/operate-contracts";

type FakeTreeNode = {
  tagName: string;
  children: FakeTreeNode[];
  getAttribute: (name: string) => string | null;
};

function createNode(
  tagName: string,
  attrs: Record<string, string> = {},
  children: FakeTreeNode[] = [],
): FakeTreeNode {
  return {
    tagName,
    children,
    getAttribute(name: string) {
      return attrs[name] ?? null;
    },
  };
}

describe("operate-contracts extractors", () => {
  it("extractDevices and extractProvidersHealth return arrays or fallback to []", () => {
    expect(extractDevices({ devices: [{ id: "dev0" }] })).toEqual([{ id: "dev0" }]);
    expect(extractDevices({})).toEqual([]);
    expect(extractProvidersHealth({ providers: [{ provider_id: "sim0" }] })).toEqual([
      { provider_id: "sim0" },
    ]);
    expect(extractProvidersHealth(undefined)).toEqual([]);
  });

  it("extractCapabilities normalizes function metadata and arg specs", () => {
    const caps = extractCapabilities({
      capabilities: {
        signals: [{ signal_id: "temp" }],
        functions: [
          { function_id: 3, function_name: "set_mode", args: { mode: { type: "string" } } },
          { function_id: 1, name: "", args: [{ name: "enabled", type: "bool" }] },
        ],
      },
    });

    expect(Array.isArray(caps.signals)).toBe(true);
    expect(caps.functions).toHaveLength(2);
    expect(caps.functions[0].function_id).toBe(1);
    expect(caps.functions[0].display_name).toBe("Function 1");
    expect(caps.functions[1].display_name).toBe("set_mode");
    expect(caps.functions[1].args[0]).toEqual({
      name: "mode",
      type: "string",
      required: true,
      min: undefined,
      max: undefined,
      allowed_values: undefined,
    });
  });

  it("normalizeFunctionArgs supports array and object arg forms", () => {
    expect(
      normalizeFunctionArgs([
        { name: "setpoint", type: "double" },
        { name: "enabled", type: "bool" },
      ]),
    ).toEqual([
      {
        name: "setpoint",
        type: "double",
        required: true,
        min: undefined,
        max: undefined,
        allowed_values: undefined,
      },
      {
        name: "enabled",
        type: "bool",
        required: true,
        min: undefined,
        max: undefined,
        allowed_values: undefined,
      },
    ]);

    expect(
      normalizeFunctionArgs({
        z: { type: "double" },
        a: { type: "string" },
      }),
    ).toEqual([
      {
        name: "a",
        type: "string",
        required: true,
        min: undefined,
        max: undefined,
        allowed_values: undefined,
      },
      {
        name: "z",
        type: "double",
        required: true,
        min: undefined,
        max: undefined,
        allowed_values: undefined,
      },
    ]);
  });

  it("normalizeArgSpec returns empty sentinel when arg name cannot be resolved", () => {
    expect(normalizeArgSpec({}, "")).toEqual({
      name: "",
      type: "string",
      required: true,
    });
  });

  it("extractDeviceStateValues maps timestamp fields deterministically", () => {
    const values = extractDeviceStateValues({
      values: [
        { signal_id: "temp", timestamp_ms: 2000 },
        { signal_id: "ph", timestamp_epoch_ms: 1234 },
      ],
    });
    expect(values[0].timestamp_ms).toBe(2000);
    expect(values[1].timestamp_ms).toBe(1234);
  });

  it("extractMode, extractRuntimeStatus, extractAutomationStatus, and extractAutomationTree apply defaults", () => {
    expect(extractMode({ mode: "AUTO" })).toBe("AUTO");
    expect(extractMode({})).toBeNull();

    expect(extractRuntimeStatus({})).toEqual({
      status: {},
      mode: "UNKNOWN",
      uptime_seconds: 0,
      polling_interval_ms: 0,
      device_count: 0,
      providers: [],
    });

    expect(
      extractAutomationStatus({
        enabled: true,
        active: false,
        bt_status: "RUNNING",
        total_ticks: 99,
      }),
    ).toEqual({
      enabled: true,
      active: false,
      bt_status: "RUNNING",
      last_tick_ms: 0,
      ticks_since_progress: 0,
      total_ticks: 99,
      last_error: null,
      error_count: 0,
      current_tree: "",
    });

    expect(extractAutomationTree({ tree: "<BehaviorTree/>" })).toBe("<BehaviorTree/>");
    expect(extractAutomationTree({})).toBe("");
  });

  it("normalizeParameterType and extractParameters keep only valid named parameters", () => {
    expect(normalizeParameterType("double")).toBe("double");
    expect(normalizeParameterType("bytes")).toBeNull();

    expect(
      extractParameters({
        parameters: [
          { name: " zeta ", type: "double" },
          { name: "alpha", type: "bytes" },
          { name: "", type: "string" },
          { type: "string" },
        ],
      }),
    ).toEqual([
      { name: "alpha", type: "bytes" },
      { name: "zeta", type: "double" },
    ]);
  });

  it("normalizes provider quality and deriveOperateAvailability decisions", () => {
    expect(normalizeProviderHealthQuality("READY")).toBe("OK");
    expect(normalizeProviderHealthQuality("FAULT")).toBe("FAULT");
    expect(normalizeProviderHealthQuality("STALE")).toBe("UNAVAILABLE");
    expect(normalizeProviderHealthQuality("weird")).toBe("UNKNOWN");

    expect(deriveOperateAvailability({ running: false, active_project: null }, "alpha")).toEqual({
      available: false,
      reason: "stopped",
      message: "Runtime is stopped. Start runtime from Commission to operate this project.",
      runningProject: "",
    });

    expect(deriveOperateAvailability({ running: true, active_project: "beta" }, "alpha")).toEqual({
      available: false,
      reason: "different_project",
      message: 'Runtime is running for project "beta". Stop it before operating "alpha".',
      runningProject: "beta",
    });

    expect(deriveOperateAvailability({ running: true, active_project: "alpha" }, "alpha")).toEqual({
      available: true,
      reason: "available",
      message: "",
      runningProject: "alpha",
    });
  });
});

describe("coerceParameterValue", () => {
  it("coerces valid values for each supported type", () => {
    expect(
      coerceParameterValue({
        type: "double",
        rawValue: "2.5",
        min: "2",
        max: "3",
        allowedValues: undefined,
      }),
    ).toBe(2.5);
    expect(
      coerceParameterValue({
        type: "int64",
        rawValue: "42",
        min: "0",
        max: "100",
        allowedValues: undefined,
      }),
    ).toBe(42);
    expect(
      coerceParameterValue({
        type: "bool",
        rawValue: "TRUE",
        min: undefined,
        max: undefined,
        allowedValues: undefined,
      }),
    ).toBe(true);
    expect(
      coerceParameterValue({
        type: "bool",
        rawValue: "false",
        min: undefined,
        max: undefined,
        allowedValues: undefined,
      }),
    ).toBe(false);
    expect(
      coerceParameterValue({
        type: "string",
        rawValue: "AUTO",
        min: undefined,
        max: undefined,
        allowedValues: ["MANUAL", "AUTO"],
      }),
    ).toBe("AUTO");
  });

  it("rejects unsupported types and invalid values", () => {
    expect(
      () =>
        coerceParameterValue({
          type: "bytes",
          rawValue: "AAAA",
          min: undefined,
          max: undefined,
          allowedValues: undefined,
        }),
    ).toThrow(
      /unsupported parameter type/i,
    );
    expect(
      () =>
        coerceParameterValue({
          type: "double",
          rawValue: "nanx",
          min: undefined,
          max: undefined,
          allowedValues: undefined,
        }),
    ).toThrow(/invalid number/i);
    expect(() => coerceParameterValue({ type: "double", rawValue: "1.0", min: "2.0" })).toThrow(
      /below minimum/i,
    );
    expect(() => coerceParameterValue({ type: "double", rawValue: "9.0", max: "5.0" })).toThrow(
      /above maximum/i,
    );
    expect(
      () =>
        coerceParameterValue({
          type: "int64",
          rawValue: "not-int",
          min: undefined,
          max: undefined,
          allowedValues: undefined,
        }),
    ).toThrow(
      /invalid integer/i,
    );
    expect(
      () =>
        coerceParameterValue({
          type: "int64",
          rawValue: "9223372036854775808",
          min: undefined,
          max: undefined,
          allowedValues: undefined,
        }),
    ).toThrow(
      /out-of-range int64/i,
    );
    expect(
      () =>
        coerceParameterValue({
          type: "int64",
          rawValue: "9007199254740992",
          min: undefined,
          max: undefined,
          allowedValues: undefined,
        }),
    ).toThrow(
      /browser-safe range/i,
    );
    expect(
      () =>
        coerceParameterValue({
          type: "string",
          rawValue: "UNKNOWN",
          min: undefined,
          max: undefined,
          allowedValues: ["MANUAL", "AUTO"],
        }),
    ).toThrow(/must be one of/i);
  });
});

describe("renderBtOutline", () => {
  it("returns a no-tree message when BehaviorTree node is absent", () => {
    const fakeDocument = {
      querySelector() {
        return null;
      },
    } as unknown as Document;

    expect(renderBtOutline(fakeDocument)).toBe("No BehaviorTree found.");
  });

  it("renders a readable outline for nested tree nodes", () => {
    const tree = createNode("BehaviorTree", { name: "main" }, [
      createNode("Sequence"),
      createNode("Fallback", {}, [createNode("Action", { name: "SetMode" })]),
    ]);
    const fakeDocument = {
      querySelector(selector: string) {
        return selector === "BehaviorTree" ? tree : null;
      },
    } as unknown as Document;

    const outline = renderBtOutline(fakeDocument);
    expect(outline).toContain('BehaviorTree "main"');
    expect(outline).toContain("|- Sequence");
    expect(outline).toContain("\\- Fallback");
    expect(outline).toContain('"SetMode"');
  });
});

describe("normalizeFunctionSpecs", () => {
  it("sorts by function_id and then display name for ties", () => {
    const result = normalizeFunctionSpecs([
      { function_id: 2, function_name: "B" },
      { function_id: 1, function_name: "Z" },
      { function_id: 1, function_name: "A" },
    ]);
    expect(result.map((entry) => `${entry.function_id}:${entry.display_name}`)).toEqual([
      "1:A",
      "1:Z",
      "2:B",
    ]);
  });
});
