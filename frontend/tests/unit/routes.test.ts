import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { EXACT_ROUTES, WORKSPACES, parseRoute } from "../../src/lib/routes";

// Shared with tests/unit/test_shell_route_support.py, which asserts the
// server's route lists equal the same fixture.
const fixture = JSON.parse(
  readFileSync(new URL("../../../tests/fixtures/spa-routes.json", import.meta.url), "utf-8"),
) as {
  exact_routes: string[];
  workspaces: string[];
  client_routes: string[];
  not_client_routes: string[];
};

describe("parseRoute / server SPA fallback parity", () => {
  it("routes exactly the paths and workspaces the server falls back for", () => {
    expect([...EXACT_ROUTES]).toEqual(fixture.exact_routes);
    expect(WORKSPACES).toEqual(fixture.workspaces);
  });

  it.each(fixture.client_routes)("accepts client route %s", (path) => {
    expect(parseRoute(path)).not.toBeNull();
  });

  it.each(fixture.not_client_routes)("rejects non-route %s", (path) => {
    expect(parseRoute(path)).toBeNull();
  });
});
