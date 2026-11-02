import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Home from '../../src/routes/Home.svelte';
import { jsonResponse } from './helpers';

describe('Home.svelte', () => {
  it('renders projects and preselects first template', async () => {
    render(Home, {
      props: {
        projects: [{ name: 'alpha', meta: {} }],
        templates: [
          { id: 'sim-quickstart', meta: { name: 'Sim Quickstart' } },
          { id: 'bioreactor', meta: { name: 'Bioreactor' } },
        ],
        onNavigate: vi.fn(),
        onProjectsRefreshed: vi.fn(async () => {}),
      },
    });

    expect(screen.getByText('alpha')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument();

    const templateSelect = screen.getByLabelText('Template') as HTMLSelectElement;
    await waitFor(() => {
      expect(templateSelect.value).toBe('sim-quickstart');
    });
  });

  it('shows a template that failed to load as unavailable, with its error', async () => {
    render(Home, {
      props: {
        projects: [],
        templates: [
          { id: 'broken', meta: { name: 'broken' }, error: 'machine-profile.yaml: root must be a mapping' },
          { id: 'sim-quickstart', meta: { name: 'Sim Quickstart' } },
        ],
        onNavigate: vi.fn(),
        onProjectsRefreshed: vi.fn(async () => {}),
      },
    });

    const option = screen.getByRole('option', { name: /broken/ }) as HTMLOptionElement;
    expect(option.disabled).toBe(true);
    expect(option).toHaveTextContent('root must be a mapping');

    const templateSelect = screen.getByLabelText('Template') as HTMLSelectElement;
    await waitFor(() => {
      expect(templateSelect.value).toBe('sim-quickstart');
    });
  });

  it('shows validation error and does not call API for invalid project name', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    render(Home, {
      props: {
        projects: [],
        templates: [{ id: 'sim-quickstart', meta: { name: 'Sim Quickstart' } }],
        onNavigate: vi.fn(),
        onProjectsRefreshed: vi.fn(async () => {}),
      },
    });

    const nameInput = screen.getByLabelText('Project name') as HTMLInputElement;
    await fireEvent.input(nameInput, { target: { value: 'invalid name' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Create Project' }));

    expect(
      screen.getByText('Project name must be 1-64 chars: letters, digits, hyphens, underscores.'),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('creates a project then refreshes list and navigates to Compose', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(201, { ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const onNavigate = vi.fn();
    const onProjectsRefreshed = vi.fn(async () => {});

    render(Home, {
      props: {
        projects: [],
        templates: [{ id: 'sim-quickstart', meta: { name: 'Sim Quickstart' } }],
        onNavigate,
        onProjectsRefreshed,
      },
    });

    const nameInput = screen.getByLabelText('Project name') as HTMLInputElement;
    await fireEvent.input(nameInput, { target: { value: 'alpha_1' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Create Project' }));

    await waitFor(() => {
      expect(onProjectsRefreshed).toHaveBeenCalledTimes(1);
      expect(onNavigate).toHaveBeenCalledWith('/projects/alpha_1/compose');
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  function renderHome() {
    render(Home, {
      props: {
        projects: [],
        templates: [],
        runtimeStatus: { running: false, active_project: null },
        onNavigate: vi.fn(),
        onProjectsRefreshed: vi.fn(async () => {}),
      },
    });
  }

  it('shows the command that upgrades this install, and runs nothing (#535)', async () => {
    const command = '/srv/wb/venv/bin/python -m pip install --upgrade anolis-workbench==0.33.0';
    const fetchMock = vi.fn(async (_path: string) =>
      jsonResponse(200, {
        current_version: '0.32.0',
        latest_version: '0.33.0',
        update_available: true,
        error: null,
        upgrade: {
          install: 'pip',
          command,
          restart: 'sudo systemctl restart anolis-workbench',
          note: 'Run the command, then restart the workbench service.',
        },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderHome();

    await fireEvent.click(screen.getByRole('button', { name: 'Check for Updates' }));

    expect(await screen.findByText('Update available: v0.33.0')).toBeInTheDocument();
    const howTo = screen.getByTestId('upgrade-instructions');
    expect(howTo).toHaveTextContent(command);
    expect(howTo).toHaveTextContent('sudo systemctl restart anolis-workbench');
    expect(howTo).toHaveTextContent('Run the command, then restart the workbench service.');
    expect(screen.queryByRole('button', { name: /Update to/ })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/update-check']);
  });

  it('points the desktop app at the release, with no command', async () => {
    const note = 'Install the new desktop build from https://github.com/anolishq/anolis-workbench/releases/tag/v0.33.0.';
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(200, {
          current_version: '0.32.0',
          latest_version: '0.33.0',
          update_available: true,
          error: null,
          upgrade: { install: 'desktop', command: null, restart: null, note },
        }),
      ),
    );
    renderHome();

    await fireEvent.click(screen.getByRole('button', { name: 'Check for Updates' }));

    const howTo = await screen.findByTestId('upgrade-instructions');
    expect(howTo).toHaveTextContent(note);
    expect(howTo.querySelector('code')).toBeNull();
  });

  it('asks before rolling back, says the service restarts, and names a running project', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { success: true, output: '', error: null }));
    vi.stubGlobal('fetch', fetchMock);

    render(Home, {
      props: {
        projects: [],
        templates: [],
        runtimeStatus: { running: true, active_project: 'demo' },
        onNavigate: vi.fn(),
        onProjectsRefreshed: vi.fn(async () => {}),
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Rollback' }));

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(/restart/i);
    expect(dialog).toHaveTextContent('A runtime is running project "demo" on this machine.');
    // /api/status cannot tell a dev launch from the installed service.
    expect(dialog).not.toHaveTextContent(/launched from this workbench/);
    expect(fetchMock).not.toHaveBeenCalled();

    await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    await fireEvent.click(screen.getByRole('button', { name: 'Rollback' }));
    await fireEvent.click(screen.getByRole('button', { name: 'Roll back and restart' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe('/api/rollback');
  });
});
