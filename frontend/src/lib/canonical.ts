/**
 * Canonical-project helpers, mirroring `anolis_workbench/core/canonical.py`.
 *
 * The one idea to keep in mind: inside a runtime config a provider `command`
 * is NOT a host path — it is a deploy TOKEN that install.sh rewrites to
 * `{prefix}/bin/anolis-provider-<kind>`. The real host binary path lives only
 * in the project's `host_paths` and is used for dev-launch. Authoring the
 * wrong shape here means install.sh silently ships dev-relative paths, so
 * these builders exist to make the token the only thing the UI can produce.
 */
import type { ProjectDocument, RuntimeConfigDoc, UnknownRecord } from "./contracts";

export const MANUAL_VARIANT = "manual";
export const AUTOMATION_VARIANT = "automation";

/** install.sh accepts any single path segment as the build preset. */
export const DEFAULT_BUILD_PRESET = "dev-release";

/** Mirrors install.sh's provider-command rewrite pattern. */
const PROVIDER_COMMAND_RE =
  /^\.\.\/anolis-provider-([a-z0-9-]+)\/build\/[^/]+\/anolis-provider-([a-z0-9-]+)$/;

export function providerCommandToken(kind: string, preset = DEFAULT_BUILD_PRESET): string {
  return `../anolis-provider-${kind}/build/${preset}/anolis-provider-${kind}`;
}

export function projectPathToken(machineId: string, relpath: string): string {
  return `../anolis-projects/projects/${machineId}/${relpath}`;
}

/**
 * install.sh takes the provider config's stem up to the FIRST dot as the
 * installed name, so the kind has to come first.
 */
export function providerConfigFilename(kind: string, providerId: string): string {
  return `provider-${kind}.${providerId}.yaml`;
}

export function providerConfigRelpath(kind: string, providerId: string): string {
  return `config/${providerConfigFilename(kind, providerId)}`;
}

export function variantRelpath(variant: string): string {
  return `config/anolis-runtime.${variant}.yaml`;
}

/**
 * The kind install.sh will resolve from a command token, or null.
 *
 * install.sh rewrites using the SECOND capture group and re-derives the kind
 * from the rendered path, so a token whose two kind captures disagree resolves
 * to something other than what the filename implies — treat that as unusable.
 */
export function commandKind(command: unknown): string | null {
  if (typeof command !== "string") return null;
  const match = PROVIDER_COMMAND_RE.exec(command);
  if (!match || match[1] !== match[2]) return null;
  return match[2];
}

/** Slugify a project name into a machine_id (mirrors machine_id_from_name). */
export function machineIdFromName(name: string): string {
  const cleaned = name
    .trim()
    .toLowerCase()
    .replace(/_/g, "-")
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/^-+|-+$/g, "");
  return cleaned || "machine";
}

/** Which variant the composer is editing / dev-launch runs. */
export function activeVariant(doc: ProjectDocument | null): string {
  const requested = typeof doc?.launch?.variant === "string" ? doc.launch.variant : "";
  if (requested && doc?.variants?.[requested]) return requested;
  if (doc?.variants?.[MANUAL_VARIANT]) return MANUAL_VARIANT;
  const names = Object.keys(doc?.variants ?? {});
  return names[0] ?? MANUAL_VARIANT;
}

export function activeRuntimeConfig(doc: ProjectDocument | null): RuntimeConfigDoc | null {
  if (!doc) return null;
  return doc.variants?.[activeVariant(doc)] ?? null;
}

/**
 * Why this runtime config is not inert, or null — mirrors install.sh, which
 * REFUSES to install a `manual` variant that boots into automation.
 */
export function inertnessViolation(doc: RuntimeConfigDoc | null | undefined): string | null {
  if (!doc || !("automation" in doc)) return null;
  const automation = doc.automation;
  if (automation === null || automation === undefined) return null;
  if (typeof automation !== "object" || Array.isArray(automation)) {
    return "automation must be a mapping";
  }
  const enabled = (automation as UnknownRecord).enabled;
  if (typeof enabled === "string") {
    return `automation.enabled is the string "${enabled}"`;
  }
  if (enabled) return "automation.enabled is true";
  if ("mode_transition_hooks" in automation) return "automation.mode_transition_hooks is present";
  return null;
}

/** Kinds the profile pins — install.sh fetches provider binaries by these keys. */
export function pinnedKinds(doc: ProjectDocument | null): string[] {
  const providers = doc?.profile?.components?.providers;
  return providers ? Object.keys(providers).sort() : [];
}
