import { describe, expect, it } from "vitest";

import type { EstopStatus, RuntimeApiStatus, StopChainStatus } from "../../src/lib/contracts";
import { machineLine } from "../../src/lib/operate-contracts";

function status(mode: string, extra: Partial<RuntimeApiStatus> = {}): RuntimeApiStatus {
  return {
    status: { code: "OK" },
    version: "0.6.0",
    mode,
    uptime_seconds: 1,
    polling_interval_ms: 500,
    providers: [],
    device_count: 2,
    device_loss_latched: [],
    ...extra,
  } as RuntimeApiStatus;
}

const LATCHED: EstopStatus = {
  latched: true,
  software_safe_state: "hooks",
  latched_at_epoch_ms: 1,
  uncovered_actuating_functions: 0,
};

function chain(state: StopChainStatus["state"], expected_losses: string[] = []): StopChainStatus {
  return {
    state,
    reason: "",
    signal: "bread0/gpio0/chain_open",
    devices: [],
    expectations: {},
    expected_losses,
    mismatches: [],
    since_epoch_ms: null,
  };
}

describe("machineLine (wb#727)", () => {
  it("is Ready when the runtime answers and nothing needs the operator", () => {
    expect(machineLine(status("IDLE"), null, null, [])).toMatchObject({
      state: "ready",
      text: "Ready.",
      target: null,
    });
    expect(machineLine(status("MANUAL"), null, null, []).state).toBe("ready");
  });

  it("says the experiment is running in AUTO", () => {
    expect(machineLine(status("AUTO"), null, null, [])).toMatchObject({
      state: "running",
      text: "Experiment running.",
    });
  });

  it("says the software stop holds it, and to clear it", () => {
    expect(machineLine(status("IDLE"), LATCHED, null, [])).toMatchObject({
      state: "stopped",
      text: "Stopped by the software stop: clear it to continue.",
    });
  });

  it("says FAULT is left through MANUAL, why not AUTO, and links to Mode", () => {
    const line = machineLine(status("FAULT"), null, null, []);
    expect(line).toMatchObject({ state: "fault", target: "mode" });
    expect(line.text).toContain("set MANUAL to recover");
    expect(line.text).toContain("AUTO is not offered from FAULT");
  });

  it("names a tripped board and links to the trip clear", () => {
    expect(machineLine(status("MANUAL"), null, null, ["bread0/dcmt0"])).toMatchObject({
      state: "tripped",
      text: "Board tripped: clear the trip on bread0/dcmt0.",
      target: "watchdog-trips",
    });
  });

  it("names a lost device and says to check its power", () => {
    expect(
      machineLine(status("MANUAL", { device_loss_latched: ["ezo0/ph0"] }), null, null, []),
    ).toMatchObject({
      state: "device_lost",
      text: "Device lost: check its power (ezo0/ph0).",
      target: "provider-health",
    });
  });

  it("does not call a device the emergency stop cut a lost device", () => {
    const line = machineLine(
      status("MANUAL", { device_loss_latched: ["bread0/dcmt0"] }),
      null,
      chain("clear", ["bread0/dcmt0"]),
      [],
    );
    expect(line.state).toBe("ready");
  });

  it("says the emergency stop is in force before anything else", () => {
    expect(
      machineLine(status("FAULT"), LATCHED, chain("in_force"), ["bread0/dcmt0"]),
    ).toMatchObject({
      state: "emergency_stop",
      text: "Emergency stop in force: release it to continue.",
    });
  });

  it("puts the software stop before FAULT, FAULT before a trip, and a trip before a lost device", () => {
    const lost = { device_loss_latched: ["ezo0/ph0"] };
    expect(machineLine(status("FAULT", lost), LATCHED, null, ["bread0/dcmt0"]).state).toBe(
      "stopped",
    );
    expect(machineLine(status("FAULT", lost), null, null, ["bread0/dcmt0"]).state).toBe("fault");
    expect(machineLine(status("MANUAL", lost), null, null, ["bread0/dcmt0"]).state).toBe("tripped");
  });

  it("says nothing it cannot know when the runtime does not answer", () => {
    expect(machineLine(null, null, null, [])).toMatchObject({
      state: "unavailable",
      text: "The runtime is not answering.",
      target: "runtime-status",
    });
  });
});
