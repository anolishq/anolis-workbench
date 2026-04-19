<script lang="ts">
  import { fetchJson } from "../lib/api";

  let {
    projects,
    templates,
    onNavigate,
    onProjectsRefreshed,
  }: {
    projects: Array<{ name: string }>;
    templates: Array<{ id: string; meta?: { name?: string } }>;
    onNavigate: (path: string) => void;
    onProjectsRefreshed: () => Promise<void> | void;
  } = $props();

  let createName = $state<string>("");
  let createTemplate = $state<string>("");
  let createError = $state<string>("");
  let creating = $state<boolean>(false);

  $effect(() => {
    if (templates.length > 0 && !createTemplate) {
      createTemplate = templates[0].id;
    }
  });

  function validProjectName(name: string): boolean {
    return /^[a-zA-Z0-9_-]{1,64}$/.test(name);
  }

  async function handleCreate() {
    createError = '';
    if (!validProjectName(createName)) {
      createError = 'Project name must be 1-64 chars: letters, digits, hyphens, underscores.';
      return;
    }
    if (!createTemplate) {
      createError = 'Template is required.';
      return;
    }
    creating = true;
    try {
      await fetchJson('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: createName, template: createTemplate }),
      });
      const name = createName;
      createName = '';
      await onProjectsRefreshed();
      onNavigate(`/projects/${encodeURIComponent(name)}/compose`);
    } catch (err) {
      createError = err instanceof Error ? err.message : 'Failed to create project';
    } finally {
      creating = false;
    }
  }
</script>

<section id="workspace-home" class="workspace visible">
  <div class="workspace-header">
    <h1>Commissioning Workbench</h1>
    <p>Choose a project to start or create a new one from a template.</p>
  </div>

  <div class="home-grid">
    <div class="home-card">
      <h2>Existing Projects</h2>
      {#if projects.length === 0}
        <ul class="home-project-list">
          <li class="placeholder">No projects yet.</li>
        </ul>
      {:else}
        <ul class="home-project-list">
          {#each projects as project (project.name)}
            <li class="home-project-item">
              <span>{project.name}</span>
              <button
                type="button"
                class="btn-secondary btn-sm"
                onclick={() =>
                  onNavigate(`/projects/${encodeURIComponent(project.name)}/compose`)}
              >Open</button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="home-card">
      <h2>Create Project</h2>
      <div class="form-group">
        <label for="create-project-name">Project name</label>
        <input
          id="create-project-name"
          type="text"
          maxlength="64"
          placeholder="my-system"
          autocomplete="off"
          spellcheck="false"
          bind:value={createName}
        />
      </div>
      <div class="form-group">
        <label for="create-project-template">Template</label>
        <select
          id="create-project-template"
          bind:value={createTemplate}
          disabled={templates.length === 0}
        >
          {#if templates.length === 0}
            <option value="">No templates available</option>
          {:else}
            {#each templates as t}
              <option value={t.id}>{t.meta?.name ?? t.id}</option>
            {/each}
          {/if}
        </select>
      </div>
      <button
        id="btn-create-project"
        type="button"
        class="btn-primary"
        disabled={creating}
        onclick={handleCreate}
      >{creating ? 'Creating…' : 'Create Project'}</button>
      {#if createError}
        <p class="field-error">{createError}</p>
      {/if}
    </div>
  </div>
</section>
