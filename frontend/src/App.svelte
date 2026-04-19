<script lang="ts">
  import { onMount } from "svelte";
  import Home from "./routes/Home.svelte";
  import Compose from "./routes/Compose.svelte";
  import Commission from "./routes/Commission.svelte";
  import Operate from "./routes/Operate.svelte";
  import { fetchJson } from "./lib/api";
  import { describeCrossProjectRunningBanner, evaluateNavigationPrompts } from "./lib/guards";

  type WorkspaceName = "compose" | "commission" | "operate";
  type Route = { path: string; project: string | null; workspace: WorkspaceName | null };
  type TemplateEntry = { id: string; meta?: { name?: string } };
  type ProjectEntry = { name: string };
  type NavigateOptions = {
    replaceHistory?: boolean;
    historyAlreadySet?: boolean;
    bypassGuards?: boolean;
  };
  const WORKSPACES: WorkspaceName[] = ["compose", "commission", "operate"];

  // ── State ────────────────────────────────────────────────────────────────
  let catalog = $state<Record<string, any> | null>(null);
  let templates = $state<TemplateEntry[]>([]);
  let projects = $state<ProjectEntry[]>([]);
  let runtimeStatus = $state<Record<string, any>>({});
  let projectName = $state<string | null>(null);
  let system = $state<Record<string, any> | null>(null);
  let workspace = $state<WorkspaceName | null>(null);
  let dirty = $state<boolean>(false);
  let currentPath = $state<string>("/");
  let commissionRunningForCurrent = $state<boolean>(false);
  let lastNavigationId = 0;

  // ── Derived ──────────────────────────────────────────────────────────────
  const running = $derived(Boolean(runtimeStatus?.running));
  const runningProject = $derived(
    typeof runtimeStatus?.active_project === "string" ? runtimeStatus.active_project : null,
  );
  const crossProjectBanner = $derived(
    describeCrossProjectRunningBanner({
      activeProject: projectName,
      runtimeRunning: running,
      runningProject,
    }),
  );

  // ── Navigation ────────────────────────────────────────────────────────────
  function parseRoute(path: string): Route | null {
    if (path === "/") return { path: "/", project: null, workspace: null };
    const match = path.match(/^\/projects\/([^/]+)(?:\/(compose|commission|operate))?\/?$/);
    if (!match) return null;
    const project = decodeURIComponent(match[1]);
    const ws = (match[2] || "compose") as WorkspaceName;
    return { path: `/projects/${encodeURIComponent(project)}/${ws}`, project, workspace: ws };
  }

  function confirmNavigation(route: Route): boolean {
    const prompts = evaluateNavigationPrompts({
      dirty,
      currentProject: projectName,
      currentWorkspace: workspace,
      nextProject: route.project,
      nextWorkspace: route.workspace || "compose",
      runtimeRunning: running,
      runningProject: runningProject ?? "",
    });
    for (const p of prompts) {
      if (!window.confirm(p.message)) return false;
    }
    return true;
  }

  async function navigateTo(path: string, {
    replaceHistory = false,
    historyAlreadySet = false,
    bypassGuards = false,
  }: NavigateOptions = {}): Promise<boolean> {
    const route = parseRoute(path);
    if (!route) {
      if (!historyAlreadySet) history.replaceState({}, "", "/");
      return navigateTo("/", { replaceHistory: true, historyAlreadySet: true, bypassGuards });
    }

    if (!bypassGuards && !confirmNavigation(route)) return false;

    const navId = ++lastNavigationId;
    if (!historyAlreadySet) {
      if (replaceHistory) history.replaceState({}, "", route.path);
      else history.pushState({}, '', route.path);
    }

    const projectChanged = route.project !== projectName;
    if (projectChanged) dirty = false;

    if (projectChanged && route.project) {
      const loaded = await loadProject(route.project);
      if (!loaded) {
        if (navId !== lastNavigationId) return false;
        history.replaceState({}, "", "/");
        await navigateTo("/", { replaceHistory: true, historyAlreadySet: true, bypassGuards: true });
        return false;
      }
    }

    if (!route.project) {
      projectName = null;
      system = null;
      workspace = null;
      currentPath = "/";
      return true;
    }

    if (projectChanged) {
      projectName = route.project;
      commissionRunningForCurrent = false;
    }

    workspace = route.workspace || "compose";
    currentPath = route.path;
    return true;
  }

  async function loadProject(name: string): Promise<boolean> {
    try {
      system = await fetchJson(`/api/projects/${encodeURIComponent(name)}`);
      projectName = name;
      return true;
    } catch {
      return false;
    }
  }

  async function refreshStatus(): Promise<void> {
    try {
      const status = await fetchJson<Record<string, any>>("/api/status");
      runtimeStatus = status;
      const operatorUiBase = status?.composer?.operator_ui_base;
      if (typeof operatorUiBase === "string" && operatorUiBase.trim()) {
        (window as any).__ANOLIS_COMPOSER__ = {
          ...(((window as any).__ANOLIS_COMPOSER__ ?? {}) as Record<string, any>),
          operatorUiBase: operatorUiBase.trim(),
        };
      }
      const nowRunningForCurrent =
        Boolean(status?.running) && status?.active_project === projectName;
      if (workspace === "commission" && nowRunningForCurrent !== commissionRunningForCurrent) {
        commissionRunningForCurrent = nowRunningForCurrent;
      }
    } catch {
      // non-fatal; keep prior status
    }
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  onMount(() => {
    let statusInterval: ReturnType<typeof setInterval> | null = null;

    const handlePopState = () => {
      void navigateTo(window.location.pathname, {
        replaceHistory: true,
        historyAlreadySet: true,
      }).then((ok) => {
        if (!ok) history.pushState({}, "", currentPath);
      });
    };

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };

    const init = async (): Promise<void> => {
      await Promise.all([
        fetchJson<Record<string, any>>("/api/catalog").then((c) => { catalog = c; }).catch(() => {}),
        fetchJson<TemplateEntry[]>("/api/templates").then((t) => { templates = t; }).catch(() => {}),
        fetchJson<ProjectEntry[]>("/api/projects").then((p) => { projects = p; }).catch(() => {}),
        refreshStatus(),
      ]);

      await navigateTo(window.location.pathname, { replaceHistory: true, bypassGuards: true });
      statusInterval = setInterval(() => void refreshStatus(), 2000);
    };

    void init();

    window.addEventListener("popstate", handlePopState);
    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      if (statusInterval !== null) clearInterval(statusInterval);
      window.removeEventListener("popstate", handlePopState);
      window.removeEventListener("beforeunload", handleBeforeUnload);
    };
  });

  // ── UI event handlers ─────────────────────────────────────────────────────
  function onProjectSelect(event: any): void {
    const selected = String(event.target.value ?? "");
    if (!selected) {
      void navigateTo("/");
      return;
    }
    const ws = workspace || "compose";
    void navigateTo(`/projects/${encodeURIComponent(selected)}/${ws}`);
  }

  function onTabClick(ws: WorkspaceName): void {
    if (!projectName) return;
    void navigateTo(`/projects/${encodeURIComponent(projectName)}/${ws}`);
  }

  async function onProjectsRefreshed(): Promise<void> {
    projects = await fetchJson('/api/projects').catch(() => projects);
  }
</script>

<header id="shell-topbar">
  <div class="brand-wrap">
    <button id="btn-home" class="ghost-btn" type="button" onclick={() => void navigateTo('/')}>
      Anolis Workbench
    </button>
  </div>

  <div class="project-wrap">
    <label for="project-selector" class="topbar-label">Project</label>
    <select id="project-selector" value={projectName ?? ''} onchange={onProjectSelect}>
      <option value="">No project selected</option>
      {#each projects as project (project.name)}
        <option value={project.name}>{project.name}</option>
      {/each}
    </select>
    {#if dirty}
      <span id="unsaved-indicator" title="Unsaved changes">●</span>
    {/if}
  </div>

  <nav id="workspace-tabs" aria-label="Workspace tabs">
    {#each WORKSPACES as ws}
      <button
        type="button"
        class="tab-btn"
        class:active={workspace === ws && Boolean(projectName)}
        disabled={!projectName}
        onclick={() => onTabClick(ws)}
      >{ws.charAt(0).toUpperCase() + ws.slice(1)}</button>
    {/each}
  </nav>

  <div
    id="runtime-indicator"
    class="runtime-indicator"
    class:running={running && Boolean(runningProject)}
    class:stopped={!(running && Boolean(runningProject))}
  >
    {running && runningProject ? `Running: ${runningProject}` : 'Stopped'}
  </div>
</header>

{#if crossProjectBanner}
  <div id="global-banner" class="global-banner">{crossProjectBanner}</div>
{/if}

<main id="shell-main">
  {#if !projectName}
    <Home
      {projects}
      {templates}
      onNavigate={(path) => void navigateTo(path, { bypassGuards: true })}
      {onProjectsRefreshed}
    />
  {:else if workspace === 'compose'}
    <Compose
      {projectName}
      {system}
      {catalog}
      {runtimeStatus}
      onDirty={() => { dirty = true; }}
      onSaved={() => { dirty = false; }}
      onSystemChanged={(s) => { system = s; }}
    />
  {:else if workspace === 'commission'}
    <Commission
      {projectName}
      {system}
      {runtimeStatus}
      {commissionRunningForCurrent}
    />
  {:else if workspace === 'operate'}
    <Operate
      {projectName}
      {system}
      {runtimeStatus}
    />
  {/if}
</main>
