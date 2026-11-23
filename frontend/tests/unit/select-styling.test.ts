// A <select> that sets its own colors must draw its own box (#961).
//
// WebKitGTK, which the Linux desktop app renders with, keeps a native-looking
// <select>'s light widget and ignores its `background`, while still applying
// `color`. The header's project selector set a dark background and near-white
// text, so in the desktop app it showed white on a light box. Chromium and
// Firefox, which the browser tests run, do apply the background, so only
// `appearance: none` makes the colors hold in every engine.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "..", "src");

function svelteFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return svelteFiles(path);
    return entry.name.endsWith(".svelte") ? [path] : [];
  });
}

// Ids of every <select> in the app's components.
function selectIds(): string[] {
  return svelteFiles(SRC).flatMap((file) =>
    [...readFileSync(file, "utf8").matchAll(/<select\b[^>]*\bid="([^"{}]+)"/g)].map((m) => m[1]),
  );
}

// Declarations of each app.css rule whose selector list names `#id` itself
// (not a descendant such as `#id option`).
function rulesFor(css: string, id: string): string[] {
  const rules: string[] = [];
  for (const m of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const selectors = m[1].split(",").map((s) => s.trim());
    if (selectors.some((s) => s === `#${id}` || s.startsWith(`#${id}:`))) rules.push(m[2]);
  }
  return rules;
}

describe("select styling", () => {
  const css = readFileSync(join(SRC, "app.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

  it("finds the header's project selector", () => {
    expect(selectIds()).toContain("project-selector");
  });

  it("gives every colored select appearance: none", () => {
    for (const id of selectIds()) {
      const decls = rulesFor(css, id).join(";");
      const colored = /(^|;)\s*(background(-color)?|color)\s*:/.test(decls);
      if (!colored) continue;
      expect(decls, `#${id} sets its own colors`).toMatch(/(^|;)\s*appearance\s*:\s*none/);
    }
  });
});
