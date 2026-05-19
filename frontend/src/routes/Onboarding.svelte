<script lang="ts">
  import { fetchJson } from "../lib/api";

  let {
    onNavigate,
  }: {
    onNavigate: (path: string) => void;
  } = $props();

  type OnboardingStatus = {
    first_run: boolean;
    has_projects: boolean;
    has_runtime: boolean;
    runtime_path: string;
  };

  let status = $state<OnboardingStatus | null>(null);
  let provisionMode = $state<"local" | "remote" | null>(null);
  let remoteTarget = $state<string>("");
  let remoteError = $state<string>("");
  let starting = $state<boolean>(false);

  async function loadStatus(): Promise<void> {
    try {
      status = await fetchJson<OnboardingStatus>("/api/onboarding");
    } catch {
      // If endpoint fails, don't block — just show onboarding
      status = { first_run: true, has_projects: false, has_runtime: false, runtime_path: "" };
    }
  }

  async function startLocalInstall(): Promise<void> {
    starting = true;
    try {
      const result = await fetchJson<{ job_id: string }>("/api/provision/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      onNavigate(`/onboarding/progress/${result.job_id}`);
    } catch (err) {
      starting = false;
    }
  }

  function showRemoteForm(): void {
    provisionMode = "remote";
  }

  async function startRemoteInstall(): Promise<void> {
    remoteError = "";
    if (!remoteTarget.includes("@")) {
      remoteError = "Enter target as user@hostname";
      return;
    }
    starting = true;
    try {
      const result = await fetchJson<{ job_id: string }>("/api/provision/remote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: remoteTarget }),
      });
      onNavigate(`/onboarding/progress/${result.job_id}`);
    } catch (err) {
      remoteError = err instanceof Error ? err.message : "Failed to start remote provision";
      starting = false;
    }
  }

  function skip(): void {
    onNavigate("/");
  }

  // Load status on component init
  loadStatus();
</script>

<section id="onboarding" class="workspace visible">
  <div class="onboarding-container">
    <div class="onboarding-header">
      <h1>Welcome to Anolis Workbench</h1>
      <p>No system is set up yet. Let's get started.</p>
    </div>

    {#if provisionMode === null}
      <div class="onboarding-options">
        <button
          type="button"
          class="onboarding-card"
          disabled={starting}
          onclick={startLocalInstall}
        >
          <h2>Set up this device</h2>
          <p>Install runtime + providers directly on this machine.</p>
        </button>

        <button type="button" class="onboarding-card" disabled={starting} onclick={showRemoteForm}>
          <h2>Set up a remote device</h2>
          <p>Provision a Raspberry Pi or other target over SSH.</p>
        </button>

        <button
          type="button"
          class="onboarding-card"
          disabled={starting}
          onclick={() => onNavigate("/")}
        >
          <h2>Import a project</h2>
          <p>Open an existing project from disk.</p>
        </button>
      </div>

      <div class="onboarding-skip">
        <button type="button" class="btn-secondary" onclick={skip}>
          Skip — go to project list
        </button>
      </div>
    {:else if provisionMode === "remote"}
      <div class="onboarding-remote-form">
        <h2>Remote Device Setup</h2>
        <p>Enter the SSH target for provisioning.</p>

        <div class="form-group">
          <label for="remote-target">Target (user@host)</label>
          <input
            id="remote-target"
            type="text"
            placeholder="pi@192.168.1.10"
            autocomplete="off"
            spellcheck="false"
            bind:value={remoteTarget}
          />
        </div>

        {#if remoteError}
          <p class="field-error">{remoteError}</p>
        {/if}

        <div class="onboarding-actions">
          <button
            type="button"
            class="btn-primary"
            disabled={starting || !remoteTarget}
            onclick={startRemoteInstall}
          >
            {starting ? "Starting…" : "Start Provisioning"}
          </button>
          <button
            type="button"
            class="btn-secondary"
            disabled={starting}
            onclick={() => {
              provisionMode = null;
            }}
          >
            Back
          </button>
        </div>
      </div>
    {/if}
  </div>
</section>

<style>
  .onboarding-container {
    max-width: 600px;
    margin: 2rem auto;
    padding: 0 1rem;
  }

  .onboarding-header {
    text-align: center;
    margin-bottom: 2rem;
  }

  .onboarding-header h1 {
    margin-bottom: 0.5rem;
  }

  .onboarding-header p {
    color: var(--text-secondary, #666);
  }

  .onboarding-options {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  .onboarding-card {
    display: block;
    width: 100%;
    padding: 1.25rem 1.5rem;
    border: 1px solid var(--border-color, #ddd);
    border-radius: 8px;
    background: var(--card-bg, #fff);
    cursor: pointer;
    text-align: left;
    transition:
      border-color 0.15s,
      box-shadow 0.15s;
  }

  .onboarding-card:hover:not(:disabled) {
    border-color: var(--accent-color, #2563eb);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  }

  .onboarding-card:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .onboarding-card h2 {
    margin: 0 0 0.25rem;
    font-size: 1.1rem;
  }

  .onboarding-card p {
    margin: 0;
    color: var(--text-secondary, #666);
    font-size: 0.9rem;
  }

  .onboarding-skip {
    margin-top: 1.5rem;
    text-align: center;
  }

  .onboarding-remote-form {
    margin-top: 1rem;
  }

  .onboarding-actions {
    display: flex;
    gap: 0.75rem;
    margin-top: 1rem;
  }
</style>
