import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

const RUNNING = { running: true, active_project: "demo" };
const DEVICES = { devices: [{ provider_id: "bread0", device_id: "dcmt0", display_name: "Motor" }] };
const CAPS = {
  capabilities: {
    signals: [],
    functions: [
      // As the runtime emits them: `actuating` is true unless the category is
      // READ, so CONFIG (bread's setpoints) is actuating too.
      {
        function_id: 1,
        name: "set_open_loop",
        category: "ACTUATE",
        actuating: true,
        args: { motor1_pwm: { type: "int64", required: true } },
      },
      {
        function_id: 2,
        name: "set_mode",
        category: "CONFIG",
        actuating: true,
        args: { mode: { type: "string", required: true } },
      },
      {
        function_id: 3,
        name: "set_setpoint",
        category: "CONFIG",
        actuating: true,
        args: { motor1_target: { type: "int64", required: true } },
      },
      {
        function_id: 4,
        name: "set_trim",
        args: { trim: { type: "int64", required: true } },
      },
      {
        function_id: 5,
        name: "read_counter",
        category: "READ",
        actuating: false,
        args: { channel: { type: "int64", required: true } },
      },
    ],
  },
};

function statusBody(latched: boolean) {
  return {
    status: { code: "OK" },
    version: "0.1.51",
    mode: "MANUAL",
    providers: [],
    device_count: 1,
    estop: {
      latched,
      software_safe_state: "zero",
      latched_at_epoch_ms: latched ? 1 : null,
      uncovered_actuating_functions: 0,
    },
  };
}

/** Operate for a running `demo` with one DCMT; `onCall` answers POST /v0/call. */
function renderOperate(onCall: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      if (path === "/v0/devices") return jsonResponse(200, DEVICES);
      if (path === "/v0/devices/bread0/dcmt0/capabilities") return jsonResponse(200, CAPS);
      if (path === "/v0/runtime/status") return jsonResponse(200, statusBody(false));
      if (path === "/v0/call" && init?.method === "POST") return onCall();
      if (path === "/v0/estop" && init?.method === "POST")
        return jsonResponse(200, { latched: true, software_safe_state: "zero", actions: [] });
      return jsonResponse(200, {});
    }),
  );
  render(Operate, {
    props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
  });
}

const executeButtons = () => screen.getAllByRole("button", { name: "Execute" });

describe("Operate.svelte — function calls (#580)", () => {
  it("shows the runtime's message when a call fails", async () => {
    renderOperate(() =>
      jsonResponse(409, {
        status: { code: "FAILED_PRECONDITION", message: "Device bread0/dcmt0 not found" },
      }),
    );
    await fireEvent.input(await screen.findByLabelText(/motor1_pwm/), { target: { value: "60" } });
    await fireEvent.click(executeButtons()[0]);

    expect(await screen.findByText("Error: Device bread0/dcmt0 not found")).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 409/)).not.toBeInTheDocument();
  });

  it("for a call the latch refuses, points at Clear latch, not the runtime endpoint (#692)", async () => {
    renderOperate(() =>
      jsonResponse(409, {
        status: {
          code: "FAILED_PRECONDITION",
          message: "Actuation latched by e-stop (POST /v0/estop/clear to release)",
        },
      }),
    );
    await fireEvent.input(await screen.findByLabelText(/motor1_pwm/), { target: { value: "60" } });
    await fireEvent.click(executeButtons()[0]);

    expect(
      await screen.findByText(
        "Refused: the software stop latch holds actuation. Clear it with Clear latch… above.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/estop\/clear/)).not.toBeInTheDocument();
  });

  it("keeps a successful call's result next to its form", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderOperate(() => jsonResponse(200, { status: { code: "OK" } }));
    await fireEvent.input(await screen.findByLabelText(/motor1_pwm/), { target: { value: "60" } });
    await fireEvent.click(executeButtons()[0]);

    expect(await screen.findByText(/^Accepted at /)).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(5000);
    expect(screen.getByText(/^Accepted at /)).toBeInTheDocument();
  });

  it("on a software stop, clears every form but a READ one, setpoints included", async () => {
    renderOperate(() => jsonResponse(200, { status: { code: "OK" } }));
    const field = (name: RegExp) => screen.getByLabelText(name) as HTMLInputElement;
    await screen.findByLabelText(/motor1_pwm/);
    await fireEvent.input(field(/motor1_pwm/), { target: { value: "60" } });
    await fireEvent.input(field(/^mode/), { target: { value: "open_loop" } });
    await fireEvent.input(field(/motor1_target/), { target: { value: "400" } });
    await fireEvent.input(field(/trim/), { target: { value: "5" } });
    await fireEvent.input(field(/channel/), { target: { value: "2" } });

    await fireEvent.click(screen.getByRole("button", { name: "SOFTWARE STOP" }));

    await waitFor(() => expect(field(/motor1_pwm/).value).toBe(""));
    expect(field(/motor1_target/).value).toBe("");
    expect(field(/^mode/).value).toBe("");
    expect(field(/trim/).value).toBe("");
    expect(field(/channel/).value).toBe("2");
  });
});

describe("Operate.svelte — a string arg with allowed_values (#576)", () => {
  // As the runtime emits them from anolis#467 on.
  const ENUM_CAPS = {
    capabilities: {
      signals: [],
      functions: [
        {
          function_id: 3,
          name: "set_mode",
          category: "CONFIG",
          actuating: true,
          args: {
            mode: { type: "string", required: true, allowed_values: ["open_loop", "closed_speed"] },
          },
        },
      ],
    },
  };

  /**
   * Like renderOperate, with ENUM_CAPS; returns the POST /v0/call bodies and a
   * handle that stops the runtime or swaps the capabilities it reports.
   */
  function renderWithEnum() {
    const calls: unknown[] = [];
    const rt = { running: true, caps: ENUM_CAPS as unknown };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (path === "/api/status")
          return jsonResponse(200, rt.running ? RUNNING : { running: false, active_project: null });
        if (path === "/v0/devices") return jsonResponse(200, DEVICES);
        if (path === "/v0/devices/bread0/dcmt0/capabilities") return jsonResponse(200, rt.caps);
        if (path === "/v0/runtime/status") return jsonResponse(200, statusBody(false));
        if (path === "/v0/call" && init?.method === "POST") {
          calls.push(JSON.parse(String(init.body)));
          return jsonResponse(200, { status: { code: "OK" } });
        }
        if (path === "/v0/estop" && init?.method === "POST")
          return jsonResponse(200, { latched: true, software_safe_state: "zero", actions: [] });
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
    });
    return { calls, rt };
  }

  it("renders a select of the allowed values and sends the one chosen", async () => {
    const { calls } = renderWithEnum();
    const mode = (await screen.findByLabelText(/^mode/)) as HTMLSelectElement;

    expect(mode.tagName).toBe("SELECT");
    expect(Array.from(mode.options, (o) => o.value)).toEqual(["", "open_loop", "closed_speed"]);
    await fireEvent.change(mode, { target: { value: "closed_speed" } });
    await fireEvent.click(executeButtons()[0]);

    expect(await screen.findByText(/^Accepted at /)).toBeInTheDocument();
    expect(calls).toEqual([
      {
        provider_id: "bread0",
        device_id: "dcmt0",
        function_id: 3,
        args: { mode: { type: "string", string: "closed_speed" } },
      },
    ]);
  });

  it("sends nothing while a required select is left unchosen", async () => {
    const { calls } = renderWithEnum();
    await screen.findByLabelText(/^mode/);
    await fireEvent.click(executeButtons()[0]);

    expect(await screen.findByText("Error: Missing required argument: mode")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("refuses a choice the restarted runtime no longer allows, before the POST", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls, rt } = renderWithEnum();
    await fireEvent.change(await screen.findByLabelText(/^mode/), {
      target: { value: "closed_speed" },
    });

    rt.running = false;
    await vi.advanceTimersByTimeAsync(5000);
    await waitFor(() => expect(screen.queryByLabelText(/^mode/)).not.toBeInTheDocument());
    const narrowed = structuredClone(ENUM_CAPS);
    narrowed.capabilities.functions[0].args.mode.allowed_values = ["open_loop"];
    rt.caps = narrowed;
    rt.running = true;
    await vi.advanceTimersByTimeAsync(5000);
    await screen.findByLabelText(/^mode/);
    await fireEvent.click(executeButtons()[0]);

    expect(
      await screen.findByText("Error: mode must be one of open_loop, not 'closed_speed'"),
    ).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("clears the select on a software stop", async () => {
    renderWithEnum();
    const mode = (await screen.findByLabelText(/^mode/)) as HTMLSelectElement;
    await fireEvent.change(mode, { target: { value: "open_loop" } });

    await fireEvent.click(screen.getByRole("button", { name: "SOFTWARE STOP" }));

    await waitFor(() => expect(mode.value).toBe(""));
  });

  it("keeps a text box for a string arg with no allowed_values", async () => {
    renderOperate(() => jsonResponse(200, { status: { code: "OK" } }));

    expect((await screen.findByLabelText(/^mode/)).tagName).toBe("INPUT");
  });
});

describe("Operate.svelte — args in declared order (#807)", () => {
  const PID_CAPS = {
    capabilities: {
      signals: [],
      functions: [
        {
          function_id: 9,
          name: "set_pid",
          category: "CONFIG",
          actuating: true,
          args: {
            kd1: { type: "double", required: true, position: 2 },
            kp1: { type: "double", required: true, position: 0 },
            ki1: { type: "double", required: true, position: 1 },
          },
        },
      ],
    },
  };

  type PidArgs = Record<string, { type: string; required: boolean; position?: number }>;
  const pidCaps = (args: PidArgs) => ({
    capabilities: {
      signals: [],
      functions: [{ ...PID_CAPS.capabilities.functions[0], args }],
    },
  });

  /** A runtime that can be stopped and brought back reporting other capabilities. */
  function renderPid(caps: unknown) {
    const calls: Array<{ args: unknown }> = [];
    const rt = { running: true, caps };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (path === "/api/status")
          return jsonResponse(200, rt.running ? RUNNING : { running: false, active_project: null });
        if (path === "/v0/devices") return jsonResponse(200, DEVICES);
        if (path === "/v0/devices/bread0/dcmt0/capabilities") return jsonResponse(200, rt.caps);
        if (path === "/v0/runtime/status") return jsonResponse(200, statusBody(false));
        if (path === "/v0/call" && init?.method === "POST") {
          calls.push(JSON.parse(String(init.body)) as { args: unknown });
          return jsonResponse(200, { status: { code: "OK" } });
        }
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
    });
    return { calls, rt };
  }

  const argRows = () => Array.from(document.querySelectorAll(".function-card .arg-row"));
  const argName = (row: Element) => row.querySelector("label")?.textContent?.trim().split(" ")[0];
  const argInput = (row: Element) => row.querySelector("input") as HTMLInputElement;
  const shownValues = () => argRows().map((row) => [argName(row), argInput(row).value]);

  async function typeInto(name: string, value: string) {
    const row = argRows().find((r) => argName(r) === name) as Element;
    await fireEvent.input(argInput(row), { target: { value } });
  }

  /** Stops the runtime under the mounted page, then brings it back with `caps`. */
  async function restartWith(rt: { running: boolean; caps: unknown }, caps: unknown) {
    rt.running = false;
    await vi.advanceTimersByTimeAsync(5000);
    await waitFor(() => expect(argRows()).toEqual([]));
    rt.caps = caps;
    rt.running = true;
    await vi.advanceTimersByTimeAsync(5000);
    await waitFor(() => expect(argRows()).not.toEqual([]));
  }

  it("keeps each typed value with its arg when the runtime returns them reordered", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const noPositions = pidCaps({
      kd1: { type: "double", required: true },
      kp1: { type: "double", required: true },
      ki1: { type: "double", required: true },
    });
    const { calls, rt } = renderPid(noPositions);
    await screen.findByLabelText(/^kp1/);
    expect(argRows().map(argName)).toEqual(["kd1", "ki1", "kp1"]);
    await typeInto("kd1", "0.75");
    await typeInto("ki1", "0.25");
    await typeInto("kp1", "1.5");

    await restartWith(rt, PID_CAPS);

    expect(shownValues()).toEqual([
      ["kp1", "1.5"],
      ["ki1", "0.25"],
      ["kd1", "0.75"],
    ]);
    await fireEvent.click(executeButtons()[0]);
    await screen.findByText(/^Accepted at /);
    expect(calls.map((c) => c.args)).toEqual([
      {
        kp1: { type: "double", double: 1.5 },
        ki1: { type: "double", double: 0.25 },
        kd1: { type: "double", double: 0.75 },
      },
    ]);
  });

  it("starts an added arg empty and does not send a removed arg's value", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls, rt } = renderPid(PID_CAPS);
    await screen.findByLabelText(/^kp1/);
    await typeInto("kp1", "1.5");
    await typeInto("ki1", "0.25");
    await typeInto("kd1", "0.75");

    // ki1 is gone; an optional kf1 takes its row.
    await restartWith(
      rt,
      pidCaps({
        kd1: { type: "double", required: true, position: 2 },
        kp1: { type: "double", required: true, position: 0 },
        kf1: { type: "double", required: false, position: 1 },
      }),
    );

    expect(shownValues()).toEqual([
      ["kp1", "1.5"],
      ["kf1", ""],
      ["kd1", "0.75"],
    ]);
    await fireEvent.click(executeButtons()[0]);
    await screen.findByText(/^Accepted at /);
    expect(calls.map((c) => c.args)).toEqual([
      { kp1: { type: "double", double: 1.5 }, kd1: { type: "double", double: 0.75 } },
    ]);
  });

  it("shows the boxes in declared order and sends each value under its own name", async () => {
    const { calls } = renderPid(PID_CAPS);
    await screen.findByLabelText(/^kp1/);

    const rows = argRows();
    expect(rows.map((row) => row.querySelector("label")?.textContent?.trim())).toEqual([
      "kp1 *",
      "ki1 *",
      "kd1 *",
    ]);

    // Typed down the form, as an operator enters gains in the declared order.
    for (const [i, value] of ["1.5", "0.25", "0.75"].entries()) {
      await fireEvent.input(rows[i].querySelector("input") as HTMLInputElement, {
        target: { value },
      });
    }
    await fireEvent.click(executeButtons()[0]);
    await screen.findByText(/^Accepted at /);

    expect(calls.map((c) => c.args)).toEqual([
      {
        kp1: { type: "double", double: 1.5 },
        ki1: { type: "double", double: 0.25 },
        kd1: { type: "double", double: 0.75 },
      },
    ]);
  });
});

describe("Operate.svelte — an arg's range hint names its unit", () => {
  // ph0's calibrate as anolis 0.10.2 reports it for anolis-provider-ezo 0.5.0.
  const UNIT_CAPS = {
    capabilities: {
      signals: [],
      functions: [
        {
          function_id: 4,
          name: "calibrate",
          category: "CONFIG",
          actuating: true,
          args: {
            point: { type: "string", required: true, allowed_values: ["mid", "low", "high"] },
            reference_ph: { type: "double", required: true, min: 0.0, max: 14.0, unit: "pH" },
          },
        },
        {
          function_id: 6,
          name: "set_temperature",
          category: "CONFIG",
          actuating: true,
          args: { celsius: { type: "double", required: true, unit: "°C" } },
        },
      ],
    },
  };

  function renderWithUnits() {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === "/api/status") return jsonResponse(200, RUNNING);
        if (path === "/v0/devices") return jsonResponse(200, DEVICES);
        if (path === "/v0/devices/bread0/dcmt0/capabilities") return jsonResponse(200, UNIT_CAPS);
        if (path === "/v0/runtime/status") return jsonResponse(200, statusBody(false));
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
    });
  }

  it("shows the range with its unit", async () => {
    renderWithUnits();
    const label = (await screen.findByLabelText(/^reference_ph/))
      .closest(".arg-row")
      ?.querySelector("label");
    expect(label).toHaveTextContent("reference_ph * [0 to 14 pH]");
  });

  it("shows the unit alone when the arg has no range", async () => {
    renderWithUnits();
    const label = (await screen.findByLabelText(/^celsius/))
      .closest(".arg-row")
      ?.querySelector("label");
    expect(label).toHaveTextContent("celsius * [°C]");
  });
});
