import { describe, expect, it } from "vitest";

import {
  pushSample,
  readingSamples,
  SPARKLINE_WINDOW_MS,
  sparklineMinSpan,
  sparklinePath,
  sparklineRange,
} from "../../src/lib/sparkline";

// wb#747: each declared reading keeps its last ten minutes in the browser,
// from the state Operate already receives, and draws them as one SVG path.
// A stale or faulted point is a gap.

describe("pushSample", () => {
  it("keeps the last ten minutes and drops older points", () => {
    let h = pushSample([], { t: 0, v: 1 });
    h = pushSample(h, { t: 60_000, v: 2 });
    h = pushSample(h, { t: SPARKLINE_WINDOW_MS + 30_000, v: 3 });
    expect(h).toEqual([
      { t: 60_000, v: 2 },
      { t: SPARKLINE_WINDOW_MS + 30_000, v: 3 },
    ]);
  });

  it("does not change the history it was given", () => {
    const h = [{ t: 0, v: 1 }];
    pushSample(h, { t: 1, v: 2 });
    expect(h).toEqual([{ t: 0, v: 1 }]);
  });
});

describe("sparklinePath", () => {
  it("draws nothing for fewer than two points", () => {
    expect(sparklinePath([], 100, 20)).toBe("");
    expect(sparklinePath([{ t: 0, v: 1 }], 100, 20)).toBe("");
  });

  it("spans the width over time and the height over the values, high values at the top", () => {
    const d = sparklinePath(
      [
        { t: 0, v: 0 },
        { t: 50, v: 10 },
        { t: 100, v: 5 },
      ],
      100,
      20,
    );
    expect(d).toBe("M0 20 L50 0 L100 10");
  });

  it("starts a new segment after a gap", () => {
    const d = sparklinePath(
      [
        { t: 0, v: 1 },
        { t: 10, v: 2 },
        { t: 20, v: null },
        { t: 30, v: 1 },
        { t: 40, v: 2 },
      ],
      40,
      10,
    );
    expect(d).toBe("M0 10 L10 0 M30 10 L40 0");
  });

  it("draws flat values across the middle", () => {
    expect(
      sparklinePath(
        [
          { t: 0, v: 7 },
          { t: 10, v: 7 },
        ],
        10,
        20,
      ),
    ).toBe("M0 10 L10 10");
  });
});

describe("sparklinePath's minimum span (wb#747)", () => {
  it("draws a flat-but-noisy series near flat, not as full-height swings", () => {
    const noisy = [5.43, 5.44, 5.43, 5.44, 5.43].map((v, t) => ({ t, v }));
    const d = sparklinePath(noisy, 100, 20, sparklineMinSpan(2));
    const ys = [...d.matchAll(/[ML]\S+ (\S+)/g)].map((m) => Number(m[1]));
    expect(Math.max(...ys) - Math.min(...ys)).toBeLessThanOrEqual(2);
    expect(Math.min(...ys)).toBeGreaterThan(8);
    expect(Math.max(...ys)).toBeLessThan(12);
  });

  it("uses the values' own span once it is wider than the minimum", () => {
    const wide = [0, 10].map((v, t) => ({ t, v }));
    expect(sparklinePath(wide, 10, 20, sparklineMinSpan(2))).toBe("M0 20 L10 0");
  });

  it("is ten steps of the declared decimals, or 0.1 without them", () => {
    expect(sparklineMinSpan(2)).toBeCloseTo(0.1);
    expect(sparklineMinSpan(1)).toBeCloseTo(1);
    expect(sparklineMinSpan(0)).toBe(10);
    expect(sparklineMinSpan(undefined)).toBeCloseTo(0.1);
  });
});

describe("sparklineRange", () => {
  it("is the window's lowest and highest value, gaps left out", () => {
    expect(
      sparklineRange([
        { t: 0, v: 5.43 },
        { t: 1, v: null },
        { t: 2, v: 5.47 },
      ]),
    ).toEqual({ lo: 5.43, hi: 5.47 });
    expect(sparklineRange([{ t: 0, v: null }])).toBeNull();
  });
});

describe("readingSamples", () => {
  const operator = {
    readings: [
      { signal: "ezo0/ph0/ph_value", label: "pH" },
      { signal: "ezo0/do0/do_mg_l", label: "DO" },
      { signal: "bread0/rlht0/t1_c", label: "T" },
    ],
  };

  it("gives each declared reading's number, its source stamp, and a gap when not OK", () => {
    const samples = readingSamples(operator, {
      "ezo0/ph0": [
        {
          signal_id: "ph_value",
          value: { type: "double", double: 6.5 },
          quality: "OK",
          timestamp_epoch_ms: 100,
        },
      ],
      "ezo0/do0": [
        {
          signal_id: "do_mg_l",
          value: { type: "double", double: 7 },
          quality: "STALE",
          timestamp_epoch_ms: 200,
        },
      ],
    });
    expect(samples).toEqual([
      { signal: "ezo0/ph0/ph_value", stamp: "100", value: 6.5 },
      { signal: "ezo0/do0/do_mg_l", stamp: "200", value: null },
    ]);
  });
});
