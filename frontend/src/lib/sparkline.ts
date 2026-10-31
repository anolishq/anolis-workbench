// sparkline.ts: each declared reading's recent history, kept in the browser
// from the state Operate already receives, and drawn as one SVG path
// (wb#747). Empty on page load; long history is Grafana's.

import type { DeviceStateValue, OperatorBlock } from "./contracts";
import { typedNumber } from "./operate-contracts";

/** How much history a sparkline keeps. */
export const SPARKLINE_WINDOW_MS = 10 * 60 * 1000;

/** One point: when it arrived, and the value, or null for a gap. */
export interface Sample {
  t: number;
  v: number | null;
}

/** The history with `sample` added and points older than the window dropped. */
export function pushSample(history: Sample[], sample: Sample): Sample[] {
  const since = sample.t - SPARKLINE_WINDOW_MS;
  return [...history.filter((s) => s.t >= since), sample];
}

/**
 * The least value span a sparkline is drawn over: ten steps of the reading's
 * declared decimals, or 0.1 without them. A reading that moves by its last
 * digit then draws near flat instead of as full-height swings.
 */
export function sparklineMinSpan(decimals: number | undefined): number {
  return 10 * 10 ** -(decimals ?? 2);
}

/** The window's lowest and highest value, gaps left out, or null with none. */
export function sparklineRange(history: Sample[]): { lo: number; hi: number } | null {
  const values = history.map((s) => s.v).filter((v): v is number => v !== null);
  return values.length ? { lo: Math.min(...values), hi: Math.max(...values) } : null;
}

/**
 * SVG path data over a `width` x `height` box: time across, value up, over
 * the values' span or `minSpan`, whichever is wider, centred on the values.
 * A null point ends a segment, so a stale or faulted stretch is a gap. Under
 * two points there is no line.
 */
export function sparklinePath(
  history: Sample[],
  width: number,
  height: number,
  minSpan = 0,
): string {
  const range = sparklineRange(history);
  if (history.length < 2 || !range) return "";
  const t0 = history[0].t;
  const span = history[history.length - 1].t - t0 || 1;
  const mid = (range.lo + range.hi) / 2;
  const half = Math.max(range.hi - range.lo, minSpan) / 2;
  const lo = mid - half;
  const hi = mid + half;
  const round = (n: number) => Math.round(n * 100) / 100;
  const parts: string[] = [];
  let pen = "M";
  for (const s of history) {
    if (s.v === null) {
      pen = "M";
      continue;
    }
    const x = round(((s.t - t0) / span) * width);
    const y = round(hi === lo ? height / 2 : height - ((s.v - lo) / (hi - lo)) * height);
    parts.push(`${pen}${x} ${y}`);
    pen = "L";
  }
  return parts.join(" ");
}

/**
 * Each declared reading Operate holds a state for: its signal, a stamp that
 * changes when the reading does (its own timestamp), and its number, or null
 * when it is not a number or not OK.
 */
export function readingSamples(
  operator: OperatorBlock | undefined,
  deviceStates: Record<string, DeviceStateValue[]>,
): { signal: string; stamp: string; value: number | null }[] {
  const out: { signal: string; stamp: string; value: number | null }[] = [];
  for (const r of operator?.readings ?? []) {
    const [providerId, deviceId, signalId] = r.signal.split("/");
    const sig = deviceStates[`${providerId}/${deviceId}`]?.find((v) => v.signal_id === signalId);
    if (!sig) continue;
    const ok = String(sig.quality ?? "OK").toUpperCase() === "OK";
    const stamp = String(
      sig.timestamp_epoch_ms ?? sig.timestamp_ms ?? JSON.stringify([sig.value, sig.quality]),
    );
    out.push({ signal: r.signal, stamp, value: ok ? typedNumber(sig.value) : null });
  }
  return out;
}
