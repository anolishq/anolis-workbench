import { describe, expect, it } from "vitest";

import type {
  AutomationCallFailure,
  DeviceStateValue,
  OperatorBlock,
  ParameterDefinition,
} from "../../src/lib/contracts";
import {
  applyCallFailedEvent,
  callFailureLines,
  operatorReadings,
  operatorSettingsLines,
  signalDevice,
  unmetStartRequirements,
} from "../../src/lib/operate-contracts";

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

// anolis#686: experiment.start.require, compared by the value's type.
describe("unmetStartRequirements", () => {
  const REQUIRE = [
    { signal: "bread0/dcmt1/motor1_brake", equals: false, message: "Release the brake" },
    { signal: "ezo0/rtd0/temp_c", equals: 37, message: "Wait for 37 °C" },
    { signal: "door0/latch0/state", equals: "closed", message: "Close the door" },
  ];
  const ok = (signal_id: string, value: DeviceStateValue["value"]): DeviceStateValue => ({
    signal_id,
    value,
    quality: "OK",
  });

  it("is empty when every condition holds, whatever the typed value's shape", () => {
    expect(
      unmetStartRequirements(REQUIRE, {
        "bread0/dcmt1": [ok("motor1_brake", { type: "bool", bool: false })],
        "ezo0/rtd0": [ok("temp_c", { type: "int64", int64: 37 })],
        "door0/latch0": [ok("state", { type: "string", string: "closed" })],
      }),
    ).toEqual([]);
    expect(
      unmetStartRequirements(REQUIRE, {
        "bread0/dcmt1": [ok("motor1_brake", false)],
        "ezo0/rtd0": [ok("temp_c", { type: "double", double: 37 })],
        "door0/latch0": [ok("state", "closed")],
      }),
    ).toEqual([]);
  });

  it("names each condition that does not hold, a missing or not-OK reading included", () => {
    expect(
      unmetStartRequirements(REQUIRE, {
        "bread0/dcmt1": [ok("motor1_brake", { type: "bool", bool: true })],
        "ezo0/rtd0": [
          { signal_id: "temp_c", value: { type: "double", double: 37 }, quality: "FAULT" },
        ],
      }),
    ).toEqual([
      "Release the brake",
      "Wait for 37 °C (ezo0/rtd0/temp_c: reading is fault)",
      "Close the door (door0/latch0/state: no reading)",
    ]);
  });

  it("does not take a string or number for a bool", () => {
    expect(
      unmetStartRequirements([REQUIRE[0]], { "bread0/dcmt1": [ok("motor1_brake", "false")] }),
    ).toEqual(["Release the brake"]);
    expect(
      unmetStartRequirements([REQUIRE[0]], { "bread0/dcmt1": [ok("motor1_brake", 0)] }),
    ).toEqual(["Release the brake"]);
  });

  it("reads the device from provider/device/signal", () => {
    expect(signalDevice("bread0/dcmt1/motor1_brake")).toBe("bread0/dcmt1");
  });
});

// anolis#678: the automation calls failing now, from status and events.
describe("callFailureLines", () => {
  const failure = (n: number, fn = "pulse_motor"): AutomationCallFailure => ({
    device_handle: "bread0/dcmt1",
    function: fn,
    consecutive_failures: n,
    last_error: "motor1 brake engaged",
    last_failure_at_epoch_ms: 1,
  });

  it("names the call, its failures in a row and its last error", () => {
    expect(callFailureLines([failure(1), failure(3, "set_pwm")])).toEqual([
      "bread0/dcmt1 pulse_motor: failed once; last error: motor1 brake engaged",
      "bread0/dcmt1 set_pwm: failed 3 times in a row; last error: motor1 brake engaged",
    ]);
  });

  it("leaves out a call that has succeeded since", () => {
    expect(callFailureLines([failure(0)])).toEqual([]);
  });
});

describe("applyCallFailedEvent", () => {
  const event = {
    device_handle: "bread0/dcmt1",
    function: "pulse_motor",
    error: "Provider not found: bread0",
    consecutive_failures: 2,
    timestamp_ms: 1_700_000_000_000,
  };
  const prior: AutomationCallFailure = {
    device_handle: "bread0/dcmt1",
    function: "pulse_motor",
    consecutive_failures: 1,
    last_error: "motor1 brake engaged",
    last_failure_at_epoch_ms: 1,
  };
  const other: AutomationCallFailure = { ...prior, function: "set_pwm" };

  it("replaces the entry for the same device and function", () => {
    expect(applyCallFailedEvent([prior, other], event)).toEqual([
      {
        device_handle: "bread0/dcmt1",
        function: "pulse_motor",
        consecutive_failures: 2,
        last_error: "Provider not found: bread0",
        last_failure_at_epoch_ms: 1_700_000_000_000,
      },
      other,
    ]);
  });

  it("adds an entry for a call not failing before", () => {
    expect(applyCallFailedEvent([other], event)).toHaveLength(2);
    expect(applyCallFailedEvent([], event)[0].function).toBe("pulse_motor");
  });
});
