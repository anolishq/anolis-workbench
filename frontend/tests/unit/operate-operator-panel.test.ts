import { describe, expect, it } from "vitest";

import type { OperatorBlock, ParameterDefinition } from "../../src/lib/contracts";
import { operatorReadings, operatorSettingsLines } from "../../src/lib/operate-contracts";

// wb#728: the operator panel's readings and its line per settings group.

const OPERATOR: OperatorBlock = {
  readings: [
    { signal: "p/d/ph", label: "pH", decimals: 2 },
    { signal: "p/d/count", label: "Count", unit: "pulses" },
    { signal: "p/d/name", label: "Name" },
    { signal: "p/e/temp", label: "Temperature", unit: "°C", decimals: 1 },
  ],
  settings: [
    {
      group: "Dose",
      items: [
        { parameter: "dose_interval_s", label: "Every", unit: "s" },
        { parameter: "dose_enable", label: "On" },
      ],
    },
    { group: "Missing", items: [{ parameter: "nope", label: "Value" }] },
  ],
  experiment: { start: { enable: ["dose_enable"], mode: "AUTO" } },
};

describe("operatorReadings", () => {
  it("rounds to declared decimals, keeps undeclared values as sent, and adds the unit", () => {
    const out = operatorReadings(OPERATOR, {
      "p/d": [
        { signal_id: "ph", value: { type: "double", double: 5.409000000000001 }, quality: "OK" },
        { signal_id: "count", value: { type: "uint64", uint64: 12 }, quality: "OK" },
        { signal_id: "name", value: { type: "string", string: "rig" }, quality: "OK" },
      ],
      "p/e": [{ signal_id: "temp", value: 30.04, quality: "UNAVAILABLE" }],
    });
    expect(out).toEqual([
      { label: "pH", text: "5.41", note: "" },
      { label: "Count", text: "12 pulses", note: "" },
      { label: "Name", text: "rig", note: "" },
      { label: "Temperature", text: "30.0 °C", note: "unavailable" },
    ]);
  });

  it("names stale, faulted and missing readings", () => {
    const out = operatorReadings(OPERATOR, {
      "p/d": [
        { signal_id: "ph", value: { type: "double", double: 7 }, quality: "STALE" },
        { signal_id: "count", value: { type: "uint64", uint64: 1 }, quality: "FAULT" },
      ],
    });
    expect(out.map((r) => r.note)).toEqual(["stale", "faulted", "no reading", "no reading"]);
    expect(out[2].text).toBe("--");
  });

  it("is empty without readings", () => {
    expect(operatorReadings(undefined, {})).toEqual([]);
    expect(operatorReadings({}, {})).toEqual([]);
  });
});

describe("operatorSettingsLines", () => {
  const params = (enable: boolean): ParameterDefinition[] => [
    { name: "dose_enable", type: "bool", value: enable },
    { name: "dose_interval_s", type: "int64", value: 20 },
  ];

  it("puts the enable's on or off first, then each value with its unit", () => {
    expect(operatorSettingsLines(OPERATOR, params(true))).toEqual([
      "Dose: on, Every 20 s",
      "Missing: Value --",
    ]);
    expect(operatorSettingsLines(OPERATOR, params(false))[0]).toBe("Dose: off, Every 20 s");
  });

  it("reads an enable the runtime does not report as unknown", () => {
    expect(operatorSettingsLines(OPERATOR, [])[0]).toBe("Dose: --, Every -- s");
  });

  it("is empty without settings", () => {
    expect(operatorSettingsLines(undefined, params(true))).toEqual([]);
  });
});
