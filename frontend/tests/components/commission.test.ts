import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Commission from '../../src/routes/Commission.svelte';
import {
  createRuntimeStatus,
  createProjectDocument,
  deferred,
  jsonResponse,
  pathFromInput,
} from './helpers';

describe('Commission.svelte', () => {
  it('shows preflight result summary and details', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/projects/demo/preflight') {
        return jsonResponse(200, {
          ok: false,
          checks: [
            {
              name: 'Runtime executable',
              ok: false,
              error: 'Runtime binary not found',
              hint: 'Set runtime path in Compose',
            },
          ],
        });
      }
      return jsonResponse(200, {});
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));

    expect(await screen.findByText(/Checks failed/)).toBeInTheDocument();
    expect(screen.getByText('Runtime executable')).toBeInTheDocument();
    expect(screen.getByText('Runtime binary not found')).toBeInTheDocument();
    expect(screen.getByText('Set runtime path in Compose')).toBeInTheDocument();
  });

  it('keeps Launch disabled until a preflight has run without failures', async () => {
    let preflightOk = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/projects/demo/preflight') {
        return preflightOk
          ? jsonResponse(200, {
              ok: true,
              // The binary exists but predates --check-config.
              checks: [
                { name: 'Runtime binary exists', ok: true, error: null, hint: null },
                { name: 'Runtime --check-config', ok: null, note: 'Not yet available' },
              ],
              summary: { passed: 1, skipped: 1, failed: 0 },
            })
          : jsonResponse(200, {
              ok: false,
              checks: [{ name: 'System-level validation', ok: false, error: 'bad', hint: null }],
              summary: { passed: 0, skipped: 0, failed: 1 },
            });
      }
      return jsonResponse(200, {});
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    // Never checked: no path to a running runtime yet.
    expect(screen.getByRole('button', { name: /Launch/ })).toBeDisabled();
    expect(screen.getByText('Run a preflight check to enable Launch.')).toBeInTheDocument();

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await screen.findByText('System-level validation');
    expect(screen.getByRole('button', { name: /Launch/ })).toBeDisabled();

    // A preflight whose only non-pass is a skip ("Not yet available",
    // "Config not yet rendered") must not grey Launch out. A missing binary
    // is not a skip: its exists check fails.
    preflightOk = true;
    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await screen.findByText('Runtime binary exists');
    expect(screen.getByRole('button', { name: /Launch/ })).toBeEnabled();
  });

  it('needs a fresh preflight after a launch', async () => {
    // A runtime that stops on its own drops Commission back to the idle bar;
    // the preflight that preceded the last launch must not enable the next.
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/status') return jsonResponse(200, { running: false });
      if (path === '/api/projects/demo/preflight')
        return jsonResponse(200, {
          ok: true,
          checks: [{ name: 'Runtime binary exists', ok: true, error: null, hint: null }],
          summary: { passed: 1, skipped: 0, failed: 0 },
        });
      return jsonResponse(200, { ok: true });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await screen.findByText('Runtime binary exists');
    await fireEvent.click(screen.getByRole('button', { name: /Launch/ }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => pathFromInput(input) === '/api/projects/demo/launch')).toBe(
        true,
      );
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Launch →' })).toBeDisabled();
    });
    expect(screen.queryByText('Runtime binary exists')).not.toBeInTheDocument();
  });

  it('shows launch in-flight state while launch request is pending', async () => {
    const launchResponse = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/status') return Promise.resolve(jsonResponse(200, { running: false }));
      if (path === '/api/projects/demo/preflight')
        return Promise.resolve(
          jsonResponse(200, {
            ok: true,
            checks: [{ name: 'Runtime binary exists', ok: true, error: null, hint: null }],
            summary: { passed: 1, skipped: 0, failed: 0 },
          }),
        );
      if (path === '/api/projects/demo/launch') return launchResponse.promise;
      if (path === '/v0/runtime/status')
        return Promise.resolve(
          jsonResponse(200, {
            status: { code: 'OK' },
            mode: 'ACTIVE',
            uptime_seconds: 1,
            polling_interval_ms: 1000,
            device_count: 0,
            providers: [],
          }),
        );
      if (path === '/v0/providers/health') return Promise.resolve(jsonResponse(200, { providers: [] }));
      return Promise.resolve(jsonResponse(200, {}));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await screen.findByText('Runtime binary exists');
    await fireEvent.click(screen.getByRole('button', { name: /Launch/ }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Launching/ })).toBeDisabled();
    });

    launchResponse.resolve(jsonResponse(200, { ok: true }));

    // In-flight state ends; Launch itself then waits for a fresh preflight.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Launch →' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /Launching/ })).not.toBeInTheDocument();
  });

  it('shows stop in-flight state while stop request is pending', async () => {
    const stopResponse = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/status')
        return Promise.resolve(jsonResponse(200, { running: true, active_project: 'demo' }));
      if (path === '/v0/runtime/status')
        return Promise.resolve(
          jsonResponse(200, {
            status: { code: 'OK' },
            mode: 'ACTIVE',
            uptime_seconds: 4,
            polling_interval_ms: 1000,
            device_count: 0,
            providers: [],
          }),
        );
      if (path === '/v0/providers/health') return Promise.resolve(jsonResponse(200, { providers: [] }));
      if (path === '/api/projects/demo/stop') return stopResponse.promise;
      return Promise.resolve(jsonResponse(200, {}));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus({ running: true, active_project: 'demo' }),
        commissionRunningForCurrent: true,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Stopping/ })).toBeDisabled();
    });

    stopResponse.resolve(jsonResponse(200, { ok: true }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled();
    });
  });
});
