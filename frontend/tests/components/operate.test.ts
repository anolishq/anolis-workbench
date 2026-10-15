import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Operate from '../../src/routes/Operate.svelte';
import type { ProjectDocument } from '../../src/lib/contracts';
import { createProjectDocument, jsonResponse, pathFromInput } from './helpers';

const RUNNING = { running: true, active_project: 'demo' };

/** Operate for a running `demo`; `runtimeStatus` answers GET /v0/runtime/status. */
function renderOperate(
  runtimeStatus: Response,
  system: ProjectDocument = createProjectDocument('demo'),
) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/status') return jsonResponse(200, RUNNING);
      if (path === '/v0/runtime/status') return runtimeStatus;
      return jsonResponse(200, {});
    }),
  );
  render(Operate, { props: { projectName: 'demo', system, runtimeStatus: RUNNING } });
}

function status(estop: Record<string, unknown>, version = '0.1.44'): Response {
  return jsonResponse(200, {
    status: { code: 'OK' },
    version,
    mode: 'MANUAL',
    uptime_seconds: 1,
    polling_interval_ms: 500,
    providers: [],
    device_count: 0,
    estop: { latched: false, latched_at_epoch_ms: null, ...estop },
  });
}

describe("Operate.svelte — the safe-state note (#346)", () => {
  it('attributes the rung to the runtime version and names where to declare it', async () => {
    renderOperate(status({ software_safe_state: 'setpoints', uncovered_actuating_functions: 0 }));

    expect(await screen.findByText('As reported by runtime v0.1.44.')).toBeInTheDocument();
    const fix = screen.getByTestId('safe-state-fix');
    expect(fix).toHaveTextContent('safety.safe_state');
    expect(fix).toHaveTextContent("the project's config/anolis-runtime.manual.yaml for dev-launch");
    expect(fix).toHaveTextContent('/opt/anolis/config/runtime.yaml on an installed target');
  });

  it('shows the uncovered count under none, where it is why setpoints were not chosen', async () => {
    renderOperate(status({ software_safe_state: 'none', uncovered_actuating_functions: 2 }));

    expect(
      await screen.findByText(/2 actuating function\(s\) have no declared safe setpoint/),
    ).toBeInTheDocument();
  });

  it('shows no uncovered count under zero, which zeroes every actuator', async () => {
    renderOperate(status({ software_safe_state: 'zero', uncovered_actuating_functions: 3 }));

    expect(await screen.findByText(/Zeroes actuators that declare zero-is-safe/)).toBeInTheDocument();
    expect(screen.queryByText(/actuating function\(s\) have no declared safe setpoint/)).not.toBeInTheDocument();
  });

  it('names the variant file of the variant that launches', async () => {
    const system = createProjectDocument('demo');
    system.variants.automation = structuredClone(system.variants.manual);
    system.launch = { variant: 'automation' };
    renderOperate(status({ software_safe_state: 'zero', uncovered_actuating_functions: 0 }), system);

    expect(await screen.findByTestId('safe-state-fix')).toHaveTextContent(
      'config/anolis-runtime.automation.yaml',
    );
  });

  it('shows no uncovered count under hooks, where the runtime reports a false gap', async () => {
    renderOperate(status({ software_safe_state: 'hooks', uncovered_actuating_functions: 7 }));

    expect(await screen.findByText(/Runs the declared safe-state hooks/)).toBeInTheDocument();
    expect(screen.queryByText(/actuating function\(s\) have no declared safe setpoint/)).not.toBeInTheDocument();
  });

  it('says not observed when the runtime has not answered, and still names the file', async () => {
    renderOperate(jsonResponse(500, { error: 'runtime unreachable' }));

    expect(
      await screen.findByText('Software safe-state: not observed. The runtime has not reported it.'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('safe-state-fix')).toHaveTextContent('config/anolis-runtime.manual.yaml');
    expect(screen.queryByText(/As reported by/)).not.toBeInTheDocument();
  });
});
