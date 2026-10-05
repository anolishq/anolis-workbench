export type WorkspaceName = "compose" | "commission" | "operate";
export type Route = { path: string; project: string | null; workspace: WorkspaceName | null };

// The client route table. The server's SPA fallback (`_serve_static` in
// server/app.py) keeps the same two lists as _SPA_EXACT_ROUTES and
// _WORKSPACES. Both sides' tests compare their lists against
// tests/fixtures/spa-routes.json, so a route added to one side only fails.
export const EXACT_ROUTES = ["/", "/fleet"] as const;
export const WORKSPACES: WorkspaceName[] = ["compose", "commission", "operate"];

const PROJECT_ROUTE_RE = new RegExp(`^/projects/([^/]+)(?:/(${WORKSPACES.join("|")}))?/?$`);

export function parseRoute(path: string): Route | null {
  if ((EXACT_ROUTES as readonly string[]).includes(path)) {
    return { path, project: null, workspace: null };
  }
  const match = path.match(PROJECT_ROUTE_RE);
  if (!match) return null;
  const project = decodeURIComponent(match[1]);
  const ws = (match[2] || "compose") as WorkspaceName;
  return { path: `/projects/${encodeURIComponent(project)}/${ws}`, project, workspace: ws };
}
