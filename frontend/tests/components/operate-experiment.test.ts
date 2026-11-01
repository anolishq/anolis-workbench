import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// wb#729: the operator panel's settings, the declared groups with labels,
// units, current values and where each value came from, saved one group at a
// time; a note once after a restart that they are back at their defaults
// (anolis#681); and Start and Stop run, which run the profile's
// run.start and run.stop, Start refusing with the reason and
// the fix while the software stop is latched, a board is tripped or a device
// is lost.

const RUNNING = { running: true, active_project: "demo" };

const OPERATOR = {
  settings: [
    {
      group: "Dose 1",
      items: [
        { parameter: "dose1_enable", label: "On" },
        { parameter: "dose1_interval_s", label: "Every", unit: "s" },
        { parameter: "dose1_pulse_s", label: "For", unit: "s" },
      ],
    },
    { group: "Stirring", items: [{ parameter: "impeller_pwm", label: "Speed", unit: "%" }] },
  ],
  run: {
    start: { enable: ["impeller_enable", "dose1_enable"], mode: "AUTO" },
    stop: { disable: ["impeller_enable", "dose1_enable"], mode: "IDLE" },
  },
};

type Param = {
  name: string;
  type: string;
  value: unknown;
  source: string;
  min?: number;
  max?: number;
};

type Post = { path: string; body: Record<string, unknown> };

function fakeRuntime(
  opts: {
    mode?: string;
    latched?: boolean;
    tripped?: boolean;
    lost?: boolean;
    chainInForce?: boolean;
    down?: boolean;
    uptimes?: number[];
    operator?: unknown;
    /** Signals by "provider/device", for /v0/state. */
    states?: Record<string, { signal_id: string; value: unknown; quality: string }[]>;
    /** Holds each GET /v0/parameters after a parameter post until it settles. */
    refreshAfterSave?: Promise<void>;
    /** A run already open when the page loads (anolis#696). */
    openRun?: boolean;
    /** The runtime has no run journal: every /v0/runs answers 503. */
    noJournal?: boolean;
    /** Bool parameters to start at these values instead of the defaults. */
    switches?: Record<string, boolean>;
  } = {},
) {
  let mode = opts.mode ?? "IDLE";
  const uptimes = [...(opts.uptimes ?? [100])];
  const params: Param[] = [
    { name: "dose1_enable", type: "bool", value: false, source: "default" },
    { name: "dose1_interval_s", type: "int64", value: 20, source: "operator", min: 1, max: 86400 },
    { name: "dose1_pulse_s", type: "int64", value: 3, source: "default", min: 1, max: 60 },
    // On, so a plain Start has something switched on (anolis#701).
    { name: "impeller_enable", type: "bool", value: true, source: "default" },
    { name: "impeller_pwm", type: "int64", value: 80, source: "default", min: 0, max: 100 },
  ];
  for (const [name, value] of Object.entries(opts.switches ?? {})) {
    const p = params.find((x) => x.name === name);
    if (p) p.value = value;
  }
  const posts: Post[] = [];
  const runs: { run_id: string; state: string }[] = opts.openRun
    ? [{ run_id: "demo-0", state: "open" }]
    : [];
  // A poll's GET /v0/parameters held after reading, to land after a Save (#767).
  let holdNextRead: Promise<void> | null = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body ?? "{}")) as Record<string, unknown>;
        posts.push({ path, body });
        if (path === "/v0/parameters") {
          const p = params.find((x) => x.name === body.name);
          if (p) Object.assign(p, { value: body.value, source: "operator" });
          return jsonResponse(200, { status: { code: "OK" } });
        }
        if (path === "/v0/mode") {
          mode = String(body.mode);
          return jsonResponse(200, { status: { code: "OK" }, mode });
        }
        if (path.startsWith("/v0/runs") && opts.noJournal)
          return jsonResponse(503, {
            status: { code: "UNAVAILABLE", message: "Run registry not enabled" },
          });
        if (path === "/v0/runs") {
          if (runs.some((r) => r.state === "open"))
            return jsonResponse(409, { status: { code: "FAILED_PRECONDITION" } });
          const run = { run_id: `demo-${runs.length + 1}`, state: "open" };
          runs.unshift(run);
          return jsonResponse(200, { status: { code: "OK" }, run });
        }
        const close = path.match(/^\/v0\/runs\/([^/]+)\/close$/);
        if (close) {
          const run = runs.find((r) => r.run_id === close[1]);
          if (run) run.state = "closed";
          return jsonResponse(200, { status: { code: "OK" }, run });
        }
        return jsonResponse(200, { status: { code: "OK" } });
      }
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      if (path === "/v0/runtime/status") {
        if (opts.down) return jsonResponse(503, { status: { code: "UNAVAILABLE" } });
        const uptime = uptimes.length > 1 ? uptimes.shift() : uptimes[0];
        return jsonResponse(200, {
          status: { code: "OK" },
          version: "0.7.0",
          mode,
          uptime_seconds: uptime,
          polling_interval_ms: 500,
          providers: [],
          device_count: 2,
          device_loss_latched: opts.lost ? ["bread0/dcmt1"] : [],
          ...(opts.chainInForce
            ? {
                stop_chain: {
                  state: "in_force",
                  signal: "bread0/gpio0/chain_open",
                  reason: "open",
                },
              }
            : {}),
          estop: {
            latched: opts.latched ?? false,
            software_safe_state: "hooks",
            latched_at_epoch_ms: opts.latched ? 1_700_000_000_000 : null,
            uncovered_actuating_functions: 0,
          },
        });
      }
      if (path === "/v0/mode") return jsonResponse(200, { status: { code: "OK" }, mode });
      if (path === "/v0/runs") {
        if (opts.noJournal) return jsonResponse(503, { status: { code: "UNAVAILABLE" } });
        return jsonResponse(200, { status: { code: "OK" }, runs, count: runs.length });
      }
      if (path === "/v0/parameters") {
        if (holdNextRead) {
          const held = holdNextRead;
          holdNextRead = null;
          const read = structuredClone(params);
          await held;
          return jsonResponse(200, { status: { code: "OK" }, parameters: read });
        }
        if (opts.refreshAfterSave && posts.some((p) => p.path === "/v0/parameters"))
          await opts.refreshAfterSave;
        return jsonResponse(200, { status: { code: "OK" }, parameters: params });
      }
      if (path === "/v0/providers/health")
        return jsonResponse(200, {
          providers: [
            {
              provider_id: "bread0",
              state: "AVAILABLE",
              devices: [
                {
                  device_id: "dcmt0",
                  health: "OK",
                  reported: {
                    state: "OK",
                    metrics: { watchdog_tripped: String(opts.tripped ?? false) },
                  },
                },
                ...(opts.lost ? [] : [{ device_id: "dcmt1", health: "OK" }]),
              ],
            },
          ],
        });
      const state = path.match(/^\/v0\/state\/([^/]+)\/([^/]+)$/);
      if (state && opts.states) {
        const values = opts.states[`${state[1]}/${state[2]}`];
        if (!values) return jsonResponse(404, { status: { code: "NOT_FOUND" } });
        return jsonResponse(200, {
          status: { code: "OK" },
          provider_id: state[1],
          device_id: state[2],
          quality: "OK",
          values: values.map((v) => ({ ...v, timestamp_epoch_ms: 1_700_000_000_000, age_ms: 5 })),
        });
      }
      return jsonResponse(200, {});
    }),
  );
  const doc = createProjectDocument("demo");
  if (opts.operator !== null) doc.profile = { ...doc.profile, operator: opts.operator ?? OPERATOR };
  render(Operate, { props: { projectName: "demo", system: doc, runtimeStatus: RUNNING } });
  return {
    posts,
    mode: () => mode,
    runs,
    /** Holds the next GET /v0/parameters with the values as read; returns its release. */
    holdNextParametersRead: () => {
      let release = () => {};
      holdNextRead = new Promise<void>((r) => (release = r));
      return () => release();
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function group(name: string): HTMLElement {
  return within(screen.getByTestId("operator-settings"))
    .getByText(name)
    .closest("[data-testid='settings-group']") as HTMLElement;
}

function row(groupName: string, label: string): HTMLElement {
  return within(group(groupName))
    .getByText(label)
    .closest("[data-testid='setting']") as HTMLElement;
}

const paramPosts = (posts: Post[]) =>
  posts.filter((p) => p.path === "/v0/parameters").map((p) => p.body);

describe("Operate.svelte — the operator panel's settings (#729)", () => {
  it("shows each group's values with labels, units and where each came from, above Mode", async () => {
    fakeRuntime();

    await waitFor(() =>
      expect((within(row("Dose 1", "Every")).getByRole("textbox") as HTMLInputElement).value).toBe(
        "20",
      ),
    );
    expect(row("Dose 1", "Every")).toHaveTextContent("s");
    // An operator's value reads "changed", a default "default" (#232).
    expect(within(row("Dose 1", "Every")).getByText("changed", { selector: ".badge" })).toBeInTheDocument();
    expect(within(row("Dose 1", "Every")).queryByText("set", { selector: ".badge" })).toBeNull();
    expect(row("Dose 1", "For")).toHaveTextContent("default");
    expect(within(row("Dose 1", "On")).getByRole("checkbox")).not.toBeChecked();
    expect(row("Stirring", "Speed")).toHaveTextContent("%");

    const settings = screen.getByTestId("operator-settings");
    expect(settings.closest("details")).toBeNull();
    expect(
      settings.compareDocumentPosition(screen.getByTestId("mode-section")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("saves a group's changed values with one Save, and nothing else", async () => {
    const rt = fakeRuntime();
    const every = (await waitFor(() => {
      const el = within(row("Dose 1", "Every")).getByRole("textbox") as HTMLInputElement;
      expect(el.value).toBe("20");
      return el;
    })) as HTMLInputElement;

    await fireEvent.input(every, { target: { value: "30" } });
    // Typed, but the value it already has: not sent.
    await fireEvent.input(within(row("Dose 1", "For")).getByRole("textbox"), {
      target: { value: "3" },
    });
    await fireEvent.click(within(row("Dose 1", "On")).getByRole("checkbox"));
    await fireEvent.click(within(group("Dose 1")).getByRole("button", { name: "Save" }));

    // Every post is in once Saved shows, so the list is complete here.
    expect(await within(group("Dose 1")).findByText("Saved")).toBeInTheDocument();
    expect(paramPosts(rt.posts)).toEqual([
      { name: "dose1_enable", value: true },
      { name: "dose1_interval_s", value: 30 },
    ]);
  });

  it("keeps a ticked box ticked while Save refreshes the values (#745)", async () => {
    let release = () => {};
    const rt = fakeRuntime({ refreshAfterSave: new Promise<void>((r) => (release = r)) });
    const on = (await waitFor(() =>
      within(row("Dose 1", "On")).getByRole("checkbox"),
    )) as HTMLInputElement;
    await waitFor(() =>
      expect(within(row("Dose 1", "Every")).getByRole("textbox")).toHaveValue("20"),
    );

    await fireEvent.click(on);
    await fireEvent.click(within(group("Dose 1")).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(paramPosts(rt.posts)).toEqual([{ name: "dose1_enable", value: true }]),
    );

    // Posted, the refresh still out: the box shows what was saved.
    expect(within(row("Dose 1", "On")).getByRole("checkbox")).toBeChecked();
    release();
    expect(await within(group("Dose 1")).findByText("Saved")).toBeInTheDocument();
    expect(within(row("Dose 1", "On")).getByRole("checkbox")).toBeChecked();
  });

  it("keeps a saved box ticked when a poll that read before the Save lands after it (#767)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const rt = fakeRuntime();
    await waitFor(() => expect(within(row("Dose 1", "Every")).getByRole("textbox")).toHaveValue("20"));

    // The next poll reads the parameters now, before the Save, and lands later.
    const release = rt.holdNextParametersRead();
    await vi.advanceTimersByTimeAsync(5000);

    await fireEvent.click(within(row("Dose 1", "On")).getByRole("checkbox"));
    await fireEvent.click(within(group("Dose 1")).getByRole("button", { name: "Save" }));
    expect(await within(group("Dose 1")).findByText("Saved")).toBeInTheDocument();
    expect(paramPosts(rt.posts)).toEqual([{ name: "dose1_enable", value: true }]);
    expect(within(row("Dose 1", "On")).getByRole("checkbox")).toBeChecked();

    release();
    await vi.advanceTimersByTimeAsync(100);
    expect(within(row("Dose 1", "On")).getByRole("checkbox")).toBeChecked();
  });

  it("says Saved outside AUTO, that settings apply when automation runs (#745)", async () => {
    fakeRuntime({ mode: "IDLE" });
    const every = (await waitFor(() => {
      const el = within(row("Dose 1", "Every")).getByRole("textbox") as HTMLInputElement;
      expect(el.value).toBe("20");
      return el;
    })) as HTMLInputElement;

    await fireEvent.input(every, { target: { value: "30" } });
    await fireEvent.click(within(group("Dose 1")).getByRole("button", { name: "Save" }));

    const note = await within(group("Dose 1")).findByText("Saved");
    expect(note).toHaveAttribute("title", expect.stringContaining("when the automation runs"));
  });

  it("says Applied in AUTO, where the automation uses the values now (#745)", async () => {
    fakeRuntime({ mode: "AUTO" });
    const every = (await waitFor(() => {
      const el = within(row("Dose 1", "Every")).getByRole("textbox") as HTMLInputElement;
      expect(el.value).toBe("20");
      return el;
    })) as HTMLInputElement;
    await waitFor(() => expect(screen.getByTestId("mode-section")).toHaveTextContent("AUTO"));

    await fireEvent.input(every, { target: { value: "30" } });
    await fireEvent.click(within(group("Dose 1")).getByRole("button", { name: "Save" }));

    expect(await within(group("Dose 1")).findByText("Applied")).toBeInTheDocument();
    expect(within(group("Dose 1")).queryByText("Saved")).toBeNull();
  });

  it("refuses a value out of range, naming it, and saves none of the group", async () => {
    const rt = fakeRuntime();
    const pulse = (await waitFor(() => {
      const el = within(row("Dose 1", "For")).getByRole("textbox") as HTMLInputElement;
      expect(el.value).toBe("3");
      return el;
    })) as HTMLInputElement;

    await fireEvent.input(within(row("Dose 1", "Every")).getByRole("textbox"), {
      target: { value: "30" },
    });
    await fireEvent.input(pulse, { target: { value: "600" } });
    await fireEvent.click(within(group("Dose 1")).getByRole("button", { name: "Save" }));

    expect(
      await within(row("Dose 1", "For")).findByText(/above maximum \(60\)/),
    ).toBeInTheDocument();
    expect(paramPosts(rt.posts)).toEqual([]);
  });

  it("says once, after the runtime restarts, that the settings are back at their defaults", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fakeRuntime({ uptimes: [100, 3] });

    await screen.findByTestId("operator-settings");
    expect(screen.queryByTestId("settings-restarted")).toBeNull();
    await vi.advanceTimersByTimeAsync(5000);

    const note = await screen.findByTestId("settings-restarted");
    expect(note).toHaveTextContent("back at their defaults");
    await fireEvent.click(within(note).getByRole("button", { name: "OK" }));
    expect(screen.queryByTestId("settings-restarted")).toBeNull();
    await vi.advanceTimersByTimeAsync(5000);
    expect(screen.queryByTestId("settings-restarted")).toBeNull();
  });
});

// anolis#696: operator.run (operator.experiment is its deprecated alias).
// Settings and Start/Stop are one section, headed by run.label or "Run".
// Start run opens a run in the runtime's journal before it sets anything, so
// the run records the settings and every change after; Stop run closes it.
describe("Operate.svelte — Start and Stop run (#729, anolis#696)", () => {
  const SETTINGS = {
    dose1_enable: false,
    dose1_interval_s: 20,
    dose1_pulse_s: 3,
    impeller_pwm: 80,
  };
  const sent = (posts: Post[]) => posts.map((p) => [p.path, p.body]);

  async function click(name: string) {
    await waitFor(() =>
      expect(screen.getByTestId("machine-line")).not.toHaveTextContent(
        /not answering|Connecting/,
      ),
    );
    await fireEvent.click(within(screen.getByTestId("operator-run")).getByRole("button", { name }));
  }

  // anolis#701: Start leaves the On switches as the operator set them.
  it("Start opens a run with the settings, leaves the On switches as they are, then goes to AUTO by way of MANUAL", async () => {
    const rt = fakeRuntime({ mode: "IDLE" });
    await waitFor(() => expect(screen.getByTestId("mode-badge")).toHaveTextContent("IDLE"));
    await click("Start run");

    await waitFor(() => expect(rt.mode()).toBe("AUTO"));
    expect(sent(rt.posts)).toEqual([
      ["/v0/runs", { params: SETTINGS }],
      ["/v0/mode", { mode: "MANUAL" }],
      ["/v0/mode", { mode: "AUTO" }],
    ]);
    const feedback = await screen.findByTestId("run-feedback");
    expect(feedback).toHaveTextContent("Run started: AUTO.");
    expect(feedback).toHaveTextContent("demo-1");
  });

  it("Stop clears each enable, goes to IDLE by way of MANUAL, then closes the open run", async () => {
    const rt = fakeRuntime({ mode: "AUTO", openRun: true });
    await waitFor(() => expect(screen.getByTestId("mode-badge")).toHaveTextContent("AUTO"));
    await click("Stop run");

    await waitFor(() => expect(rt.runs[0].state).toBe("closed"));
    expect(sent(rt.posts)).toEqual([
      ["/v0/parameters", { name: "impeller_enable", value: false }],
      ["/v0/parameters", { name: "dose1_enable", value: false }],
      ["/v0/mode", { mode: "MANUAL" }],
      ["/v0/mode", { mode: "IDLE" }],
      ["/v0/runs/demo-0/close", { close_reason: "operator_stop" }],
    ]);
    expect(await screen.findByTestId("run-feedback")).toHaveTextContent("Run stopped: IDLE.");
  });

  it("Start refuses when nothing is switched on, and sends nothing (anolis#701)", async () => {
    const rt = fakeRuntime({
      mode: "IDLE",
      switches: { impeller_enable: false, dose1_enable: false },
    });
    await waitFor(() => expect(screen.getByTestId("mode-badge")).toHaveTextContent("IDLE"));
    await click("Start run");

    expect(await screen.findByTestId("run-feedback")).toHaveTextContent(
      "Refused: nothing is switched on. Tick On for at least one group.",
    );
    expect(rt.posts).toEqual([]);
  });

  it("Start reports a run already open, names it, and changes nothing", async () => {
    const rt = fakeRuntime({ mode: "IDLE", openRun: true });
    await waitFor(() => expect(screen.getByTestId("mode-badge")).toHaveTextContent("IDLE"));
    await click("Start run");

    const feedback = await screen.findByTestId("run-feedback");
    expect(feedback).toHaveTextContent(/run demo-0 is already open.*Stop run closes it/);
    expect(rt.posts).toEqual([]);
  });

  it("without a run journal, Start still runs and says the run is not recorded", async () => {
    const rt = fakeRuntime({ mode: "IDLE", noJournal: true });
    await waitFor(() => expect(screen.getByTestId("mode-badge")).toHaveTextContent("IDLE"));
    await click("Start run");

    await waitFor(() => expect(rt.mode()).toBe("AUTO"));
    expect(await screen.findByTestId("run-feedback")).toHaveTextContent(
      /Run started: AUTO\. Not recorded: the runtime has no run journal/,
    );
  });

  it("is one section, headed by the run's label, with the settings and when they apply", async () => {
    fakeRuntime({ operator: { ...OPERATOR, run: { ...OPERATOR.run, label: "Batch" } } });

    const section = await screen.findByTestId("operator-run");
    expect(within(section).getByRole("heading", { level: 3 })).toHaveTextContent("Batch");
    expect(within(section).getByTestId("operator-settings")).toBeInTheDocument();
    expect(section).toHaveTextContent("Settings apply while the automation runs (AUTO).");
  });

  it('is headed "Run" without a label', async () => {
    fakeRuntime();

    const section = await screen.findByTestId("operator-run");
    expect(within(section).getByRole("heading", { level: 3 })).toHaveTextContent(/^Run$/);
  });

  it("reads the deprecated operator.experiment as the run", async () => {
    const { run, ...rest } = OPERATOR;
    const rt = fakeRuntime({ mode: "IDLE", operator: { ...rest, experiment: run } });
    await waitFor(() => expect(screen.getByTestId("mode-badge")).toHaveTextContent("IDLE"));
    await click("Start run");

    await waitFor(() => expect(rt.mode()).toBe("AUTO"));
    expect(rt.posts[0].path).toBe("/v0/runs");
  });

  it.each([
    // [case, runtime, the machine line once its status is read, the refusal]
    [
      "the software stop is latched",
      { latched: true },
      "software stop",
      /software stop is latched.*Clear the latch/,
    ],
    ["a device is tripped", { tripped: true }, "tripped", /bread0\/dcmt0.*tripped.*Clear the trip/],
    ["a device is lost", { lost: true }, "lost", /bread0\/dcmt1.*lost.*power/],
    // From FAULT, going by way of MANUAL would clear the fault unseen.
    [
      "the runtime is in FAULT",
      { mode: "FAULT" },
      "In FAULT",
      /in FAULT.*Set MANUAL under Mode once the cause is fixed/,
    ],
    [
      "the emergency stop is in force",
      { chainInForce: true },
      "Emergency stop in force",
      /emergency stop is in force.*Release it/,
    ],
    ["the runtime is not answering", { down: true }, "not answering", /not answering/],
  ])(
    "Start refuses when %s, with the reason and the fix, and sends nothing",
    async (_c, opts, line, text) => {
      const rt = fakeRuntime({ mode: "IDLE", ...opts });
      await waitFor(() =>
        expect(screen.getByTestId("mode-badge")).toHaveTextContent(
          (opts as { mode?: string }).mode ?? "IDLE",
        ),
      );
      await waitFor(() => expect(screen.getByTestId("machine-line")).toHaveTextContent(line));
      await fireEvent.click(
        within(screen.getByTestId("operator-run")).getByRole("button", { name: "Start run" }),
      );

      expect(await screen.findByTestId("run-feedback")).toHaveTextContent(text);
      expect(rt.posts).toEqual([]);
    },
  );

  it("offers no run section without a run or settings", async () => {
    fakeRuntime({ operator: null });

    await waitFor(() => expect(screen.getByTestId("machine-line")).toHaveTextContent("Ready"));
    expect(screen.queryByTestId("operator-run")).toBeNull();
    expect(screen.queryByTestId("operator-settings")).toBeNull();
  });
});

// anolis#686: the profile's run.start.require, conditions on signals
// that must hold before Start runs. Start reads each named signal when
// clicked and refuses with the declared message while one does not hold,
// after the generic refusals.
describe("Operate.svelte — Start's declared requirements (#686)", () => {
  const REQUIRING = {
    ...OPERATOR,
    run: {
      ...OPERATOR.run,
      start: {
        ...OPERATOR.run.start,
        require: [
          {
            signal: "bread0/dcmt1/motor1_brake",
            equals: false,
            message: "Release dose pump 1's brake",
          },
          {
            signal: "bread0/dcmt1/motor2_brake",
            equals: false,
            message: "Release dose pump 2's brake",
          },
        ],
      },
    },
  };
  const brakes = (one: boolean, two: boolean, quality = "OK") => ({
    "bread0/dcmt1": [
      { signal_id: "motor1_brake", value: { type: "bool", bool: one }, quality },
      { signal_id: "motor2_brake", value: { type: "bool", bool: two }, quality },
    ],
  });

  async function start(line: string) {
    await waitFor(() => expect(screen.getByTestId("machine-line")).toHaveTextContent(line));
    await fireEvent.click(
      within(screen.getByTestId("operator-run")).getByRole("button", { name: "Start run" }),
    );
  }

  it("refuses with the declared message while a required signal does not hold", async () => {
    const rt = fakeRuntime({ mode: "IDLE", operator: REQUIRING, states: brakes(true, false) });
    await start("Ready");

    const feedback = await screen.findByTestId("run-feedback");
    expect(feedback).toHaveTextContent("Release dose pump 1's brake");
    expect(feedback).not.toHaveTextContent("pump 2");
    expect(rt.posts).toEqual([]);
  });

  it("names every requirement that does not hold", async () => {
    const rt = fakeRuntime({ mode: "IDLE", operator: REQUIRING, states: brakes(true, true) });
    await start("Ready");

    const feedback = await screen.findByTestId("run-feedback");
    expect(feedback).toHaveTextContent("Release dose pump 1's brake");
    expect(feedback).toHaveTextContent("Release dose pump 2's brake");
    expect(rt.posts).toEqual([]);
  });

  it("runs Start once every requirement holds", async () => {
    const rt = fakeRuntime({ mode: "IDLE", operator: REQUIRING, states: brakes(false, false) });
    await start("Ready");

    await waitFor(() => expect(rt.mode()).toBe("AUTO"));
  });

  it("does not count a stale reading as holding", async () => {
    const stale = fakeRuntime({
      mode: "IDLE",
      operator: REQUIRING,
      states: brakes(false, false, "STALE"),
    });
    await start("Ready");
    expect(await screen.findByTestId("run-feedback")).toHaveTextContent(
      /Release dose pump 1's brake.*stale/,
    );
    expect(stale.posts).toEqual([]);
  });

  it("refuses when a required signal has no reading", async () => {
    const rt = fakeRuntime({ mode: "IDLE", operator: REQUIRING, states: {} });
    await start("Ready");
    expect(await screen.findByTestId("run-feedback")).toHaveTextContent(
      /Release dose pump 1's brake.*no reading/,
    );
    expect(rt.posts).toEqual([]);
  });

  // anolis#701: a requirement with when follows that group's On switch.
  const SAMPLER_STYLE = {
    ...REQUIRING,
    run: {
      ...REQUIRING.run,
      start: {
        ...REQUIRING.run.start,
        require: [{ ...REQUIRING.run.start.require[0], when: "dose1_enable" }],
      },
    },
  };

  it("refuses a ticked group whose condition does not hold", async () => {
    const rt = fakeRuntime({
      mode: "IDLE",
      operator: SAMPLER_STYLE,
      states: brakes(true, false),
      switches: { dose1_enable: true },
    });
    await start("Ready");

    expect(await screen.findByTestId("run-feedback")).toHaveTextContent(
      "Release dose pump 1's brake",
    );
    expect(rt.posts).toEqual([]);
  });

  it("runs with an unticked group whose condition does not hold", async () => {
    const rt = fakeRuntime({
      mode: "IDLE",
      operator: SAMPLER_STYLE,
      states: brakes(true, false),
      switches: { dose1_enable: false },
    });
    await start("Ready");

    await waitFor(() => expect(rt.mode()).toBe("AUTO"));
  });

  it("gives a generic refusal first", async () => {
    const rt = fakeRuntime({
      mode: "IDLE",
      latched: true,
      operator: REQUIRING,
      states: brakes(true, true),
    });
    await start("software stop");

    const feedback = await screen.findByTestId("run-feedback");
    expect(feedback).toHaveTextContent("software stop is latched");
    expect(feedback).not.toHaveTextContent("brake");
    expect(rt.posts).toEqual([]);
  });
});
