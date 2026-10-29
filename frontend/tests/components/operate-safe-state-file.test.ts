import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Operate from '../../src/routes/Operate.svelte';
import { createImportedDocument, createProjectDocument, jsonResponse, pathFromInput } from './helpers';

const RUNNING = { running: true, active_project: 'rig-a' };

describe("Operate.svelte — the safe-state file for an imported project (#564, wb#692)", () => {
  it("names only the installed file, not a dev-launch file", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (pathFromInput(input) === '/api/status') return jsonResponse(200, RUNNING);
        return jsonResponse(200, {});
      }),
    );
    const system = createImportedDocument('rig-a');
    system.profile.runtime_profiles = { manual: 'config/anolis-runtime.bioreactor.manual.yaml' };
    render(Operate, { props: { projectName: 'rig-a', system, runtimeStatus: RUNNING } });
    // The file is named behind the stop row's click-open detail (#613).
    await fireEvent.click(await screen.findByRole('button', { name: 'About the software stop' }));

    // An imported project cannot dev-launch, and which variant its target
    // installed is not known here: only the installed file is named (wb#692).
    const fix = await screen.findByTestId('safe-state-fix');
    expect(fix).toHaveTextContent('/opt/anolis/config/runtime.yaml');
    expect(fix).not.toHaveTextContent('dev-launch');
    expect(fix).not.toHaveTextContent('config/anolis-runtime.bioreactor.manual.yaml');
  });

  it('names only the installed file while the runtime is an installed service', async () => {
    const service = { running: true, active_project: 'demo', external: true };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (pathFromInput(input) === '/api/status') return jsonResponse(200, service);
        return jsonResponse(200, {});
      }),
    );
    render(Operate, { props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: service } });
    await fireEvent.click(await screen.findByRole('button', { name: 'About the software stop' }));

    const fix = await screen.findByTestId('safe-state-fix');
    expect(fix).toHaveTextContent('/opt/anolis/config/runtime.yaml');
    expect(fix).not.toHaveTextContent('dev-launch');
  });
});
