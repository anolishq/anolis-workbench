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
});
