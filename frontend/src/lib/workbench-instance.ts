import type { RuntimeStatus } from "./contracts";

// The workbench instance this page was loaded from (#777): recorded from the
// first /api/status that names one, App's at page load, so a tab switched to
// Operate after the workbench restarted still compares against its load.
let loaded: string | null = null;

/**
 * Records the first instance this page sees. Returns the serving workbench's
 * package version (or "" when it names none) once a later status names a
 * different instance: the page then runs code and a project older than the
 * workbench serving it. Returns null otherwise.
 */
export function noteWorkbenchInstance(status: RuntimeStatus | null | undefined): string | null {
  const instance = status?.workbench?.instance;
  if (typeof instance !== "string" || !instance) return null;
  if (loaded === null) {
    loaded = instance;
    return null;
  }
  if (instance === loaded) return null;
  const version = status?.workbench?.package_version;
  return typeof version === "string" ? version : "";
}

/** For tests: forget the instance, as a fresh page load would. */
export function resetWorkbenchInstance(): void {
  loaded = null;
}
