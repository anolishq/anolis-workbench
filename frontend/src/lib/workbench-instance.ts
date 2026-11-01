import type { RuntimeStatus } from "./contracts";

// The workbench instance this page was loaded from (#777): recorded from the
// first /api/status that names one, App's at page load, so a tab switched to
// Operate after the workbench restarted still compares against its load.
let loaded: { instance: string; version: string } | null = null;

/** The serving workbench differs from the one this page loaded from. */
export interface WorkbenchChange {
  /** The version that loaded the page, "" when it named none. */
  from: string;
  /** The version serving now, "" when it names none. */
  to: string;
}

const versionOf = (status: RuntimeStatus | null | undefined): string => {
  const version = status?.workbench?.package_version;
  return typeof version === "string" ? version : "";
};

/**
 * Records the first instance this page sees. Returns how the serving
 * workbench differs once a later status names another instance: the page then
 * runs code and a project older than the workbench serving it. Returns null
 * otherwise.
 */
export function noteWorkbenchInstance(
  status: RuntimeStatus | null | undefined,
): WorkbenchChange | null {
  const instance = status?.workbench?.instance;
  if (typeof instance !== "string" || !instance) return null;
  if (loaded === null) {
    loaded = { instance, version: versionOf(status) };
    return null;
  }
  if (instance === loaded.instance) return null;
  return { from: loaded.version, to: versionOf(status) };
}

/** What changed, for the reload banner: an upgrade names both versions. */
export function describeWorkbenchChange(change: WorkbenchChange): string {
  return change.from && change.to && change.from !== change.to
    ? `updated from ${change.from} to ${change.to}`
    : "restarted";
}

/** For tests: forget the instance, as a fresh page load would. */
export function resetWorkbenchInstance(): void {
  loaded = null;
}
