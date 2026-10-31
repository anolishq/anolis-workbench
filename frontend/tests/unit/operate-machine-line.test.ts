import { describe, expect, it } from "vitest";

import type {
  EstopStatus,
  ProviderHealth,
  RuntimeApiStatus,
  StopChainStatus,
} from "../../src/lib/contracts";
import { deviceLosses, experimentStartRefusal, machineLine } from "../../src/lib/operate-contracts";

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

// Provider health as Operate polls it: each "provider/device" handle with its
// health, under a provider in the given state.
function health(devices: Record<string, string>, state = "AVAILABLE"): ProviderHealth[] {
  const byProvider = new Map<string, { device_id: string; health: string }[]>();
  for (const [handle, h] of Object.entries(devices)) {
    const [provider, device] = handle.split("/");
    byProvider.set(provider, [
      ...(byProvider.get(provider) ?? []),
      { device_id: device, health: h },
    ]);
  }
  return [...byProvider].map(([provider_id, devs]) => ({
    provider_id,
    state,
    devices: devs,
  })) as unknown as ProviderHealth[];
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
    expect(line.text).toBe("In FAULT: set MANUAL to recover (AUTO isn't available from FAULT).");
    expect(line.text.match(/MANUAL/g)).toHaveLength(1);
  });

  it("names a tripped device and links to the trip clear", () => {
    expect(machineLine(status("MANUAL"), null, null, ["bread0/dcmt0"])).toMatchObject({
      state: "tripped",
      text: "Device tripped: clear the trip on bread0/dcmt0.",
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

  it("calls a latched device lost while provider health says it is unreachable", () => {
    const latched = status("MANUAL", { device_loss_latched: ["ezo0/ph0"] });
    for (const providers of [
      health({ "ezo0/ph0": "UNAVAILABLE" }),
      health({ "ezo0/ph0": "STALE" }),
      health({ "ezo0/ph0": "OK" }, "UNAVAILABLE"),
      health({ "ezo0/other": "OK" }),
    ]) {
      expect(machineLine(latched, null, null, [], providers)).toMatchObject({
        state: "device_lost",
        text: "Device lost: check its power (ezo0/ph0).",
        target: "provider-health",
      });
    }
  });

  it("says a latched device that is back waits for MANUAL to AUTO, with no power advice", () => {
    // The reference rig on 2026-10-30: all three devices recovered after the
    // emergency stop was released, and stayed latched in MANUAL.
    const handles = ["bread0/dcmt0", "bread0/dcmt1", "bread0/rlht0"];
    const line = machineLine(
      status("MANUAL", { device_loss_latched: handles }),
      null,
      chain("clear"),
      [],
      health({ "bread0/dcmt0": "OK", "bread0/dcmt1": "OK", "bread0/rlht0": "WARNING" }),
    );
    expect(line).toMatchObject({
      state: "device_back",
      text:
        "bread0/dcmt0, bread0/dcmt1, bread0/rlht0 came back after a loss. " +
        "Automation won't drive them until you go MANUAL → AUTO.",
      target: "mode",
    });
    expect(line.text).not.toContain("power");
  });

  it("names only the devices still unreachable when some latched devices are back", () => {
    const line = machineLine(
      status("MANUAL", { device_loss_latched: ["bread0/dcmt0", "ezo0/ph0"] }),
      null,
      null,
      [],
      health({ "bread0/dcmt0": "OK", "ezo0/ph0": "UNAVAILABLE" }),
    );
    expect(line).toMatchObject({
      state: "device_lost",
      text: "Device lost: check its power (ezo0/ph0).",
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

// wb#729: what Start experiment refuses on, with the fix.
describe("experimentStartRefusal", () => {
  const reachable: ProviderHealth[] = [
    {
      provider_id: "bread0",
      state: "AVAILABLE",
      devices: [{ device_id: "dcmt1", health: "OK" }],
    } as ProviderHealth,
  ];

  it("is empty when nothing would stop it", () => {
    expect(experimentStartRefusal(status("IDLE"), null, null, [], [])).toBe("");
  });

  it("refuses while the runtime is not answering, the emergency stop is in force, or in FAULT", () => {
    expect(experimentStartRefusal(null, null, null, [], [])).toMatch(/not answering/);
    const down = { ...status("IDLE"), status: { code: "UNAVAILABLE" } } as RuntimeApiStatus;
    expect(experimentStartRefusal(down, null, null, [], [])).toMatch(/not answering/);
    expect(experimentStartRefusal(status("IDLE"), LATCHED, chain("in_force"), [], [])).toMatch(
      /emergency stop is in force/,
    );
    expect(experimentStartRefusal(status("FAULT"), null, null, [], [])).toMatch(
      /in FAULT\. Set MANUAL/,
    );
    expect(experimentStartRefusal(status("FAULT"), null, null, [], [], "pump refused")).toMatch(
      /in FAULT \(pump refused\)/,
    );
  });

  it("names the latch, then a tripped board, then a lost device, in that order", () => {
    const lost = status("IDLE", { device_loss_latched: ["bread0/dcmt1"] });
    expect(experimentStartRefusal(lost, LATCHED, null, ["bread0/dcmt0"], [])).toMatch(
      /software stop is latched/,
    );
    expect(experimentStartRefusal(lost, null, null, ["bread0/dcmt0"], [])).toMatch(
      /bread0\/dcmt0 tripped/,
    );
    expect(experimentStartRefusal(lost, null, null, [], [])).toMatch(/bread0\/dcmt1 lost/);
  });

  it("does not call a latched device that is back, or one the stop chain removed, lost", () => {
    const back = status("IDLE", { device_loss_latched: ["bread0/dcmt1"] });
    expect(experimentStartRefusal(back, null, null, [], reachable)).toBe("");
    // Refused for the stop itself, not for the device it removed.
    const underStop = experimentStartRefusal(
      back,
      null,
      chain("in_force", ["bread0/dcmt1"]),
      [],
      [],
    );
    expect(underStop).toMatch(/emergency stop is in force/);
    expect(underStop).not.toMatch(/lost/);
  });
});

describe("deviceLosses", () => {
  it("splits the latched handles into those still unreachable", () => {
    const s = status("AUTO", { device_loss_latched: ["bread0/dcmt0", "bread0/dcmt1"] });
    const providers = [
      {
        provider_id: "bread0",
        state: "AVAILABLE",
        devices: [{ device_id: "dcmt1", health: "WARNING" }],
      } as ProviderHealth,
    ];
    expect(deviceLosses(s, null, providers)).toEqual({
      latched: ["bread0/dcmt0", "bread0/dcmt1"],
      lost: ["bread0/dcmt0"],
    });
  });
});
