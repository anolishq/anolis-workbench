import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Compose from '../../src/routes/Compose.svelte';
import { createRuntimeStatus, createSystemConfig, deferred, jsonResponse } from './helpers';

describe('Compose.svelte', () => {
  it('shows advisory banner when runtime is active for the same project', () => {
    render(Compose, {
      props: {
        projectName: 'demo',
        system: createSystemConfig('demo'),
        catalog: null,
        runtimeStatus: createRuntimeStatus({ running: true, active_project: 'demo' }),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    expect(
      screen.getByText(/Runtime is currently running from this project\./),
    ).toBeInTheDocument();
  });

  it('shows saving state while save request is in-flight', async () => {
    const response = deferred<Response>();
    const fetchMock = vi.fn(() => response.promise);
    vi.stubGlobal('fetch', fetchMock);

    const onSaved = vi.fn();

    render(Compose, {
      props: {
        projectName: 'demo',
        system: createSystemConfig('demo'),
        catalog: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Saving/ })).toBeDisabled();
    });

    response.resolve(jsonResponse(200, { ok: true }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
      expect(onSaved).toHaveBeenCalledTimes(1);
    });
  });

  it('renders API validation errors on save failure', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(400, {
        error: 'Project validation failed',
        errors: [
          {
            source: 'schema',
            code: 'minimum',
            path: '$.topology.runtime.http_port',
            message: 'Must be >= 1',
          },
        ],
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(Compose, {
      props: {
        projectName: 'demo',
        system: createSystemConfig('demo'),
        catalog: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Project validation failed')).toBeInTheDocument();
    expect(screen.getByText(/\$\.topology\.runtime\.http_port/)).toBeInTheDocument();
    expect(screen.getByText(/Must be .*1/)).toBeInTheDocument();
  });
});
