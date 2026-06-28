import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    class FakeEventSource {
      url: string;
      onopen: ((ev: Event) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;
      private listeners: Record<string, Array<(ev: MessageEvent) => void>> = {};

      constructor(url: string) {
        this.url = url;

        setTimeout(() => {
          if (this.onopen) this.onopen(new Event('open'));
        }, 10);

        setTimeout(() => {
          const event = {
            data: JSON.stringify({
              previous_mode: 'IDLE',
              new_mode: 'ACTIVE',
              timestamp_ms: Date.now(),
            }),
          } as MessageEvent;
          (this.listeners.mode_change ?? []).forEach((cb) => cb(event));
        }, 30);
      }

      addEventListener(type: string, cb: (ev: MessageEvent) => void) {
        this.listeners[type] ??= [];
        this.listeners[type].push(cb);
      }

      close() {
        // no-op for test fake
      }
    }

    // @ts-expect-error test shim
    window.EventSource = FakeEventSource;
  });

  await page.route('**/api/catalog', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ providers: [] }) });
  });

  await page.route('**/api/templates', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ id: 'demo', meta: { name: 'Demo' } }]) });
  });

  await page.route('**/api/projects', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ name: 'alpha' }]) });
      return;
    }
    await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
  });

  await page.route('**/api/projects/alpha', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        name: 'alpha',
        topology: {
          runtime: {
            http_bind: '127.0.0.1',
            http_port: 8080,
          },
          providers: [],
        },
      }),
    });
  });

  await page.route('**/api/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        running: true,
        active_project: 'alpha',
        composer: { host: '127.0.0.1', port: 8080 },
      }),
    });
  });

  await page.route('**/v0/mode', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ mode: 'ACTIVE' }) });
  });

  await page.route('**/v0/providers/health', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ providers: [] }) });
  });

  await page.route('**/v0/devices', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ devices: [] }) });
  });

  await page.route('**/v0/runtime/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: { code: 'OK' },
        mode: 'ACTIVE',
        uptime_seconds: 10,
        polling_interval_ms: 1000,
        device_count: 0,
        providers: [],
      }),
    });
  });

  await page.route('**/v0/parameters', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ parameters: [] }) });
  });

  await page.route('**/v0/automation/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        // Engine-neutral fields the workbench reads (anolis >= v0.1.24).
        execution_status: 'running',
        execution_reason: 'waiting',
        automation_version: {
          engine_kind: 'behaviortree',
          id: 'main',
          digest: 'abcdef0123456789',
          digest_scope: 'top_level_file',
        },
        last_evaluation_at_epoch_ms: Date.now(),
        engine_diagnostics: {},
        run_id: null,
        last_error: null,
        // Deprecated behaviour-tree mirrors still emitted by the runtime.
        enabled: true,
        active: true,
        bt_status: 'RUNNING',
        last_tick_ms: Date.now(),
        ticks_since_progress: 0,
        total_ticks: 10,
        error_count: 0,
        current_tree: 'main',
      }),
    });
  });

  await page.route('**/v0/automation/tree', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tree: '<BehaviorTree name="main" />' }) });
  });

  await page.route('**/v0/events', async (route) => {
    // EventSource is mocked in the page, but keep endpoint fulfilled in case browser fetches.
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
  });
});

test('workspace shell navigation + runtime strip + operate stream state', async ({ page }) => {
  await page.goto('/');

  await expect(page.locator('#runtime-indicator')).toContainText('Running: alpha');
  await expect(page.locator('#workspace-home')).toBeVisible();

  await page.getByRole('button', { name: 'Open' }).first().click();
  await expect(page).toHaveURL(/\/projects\/alpha\/compose$/);
  await expect(page.locator('#workspace-compose')).toBeVisible();

  await page.getByRole('button', { name: 'Commission' }).click();
  await expect(page).toHaveURL(/\/projects\/alpha\/commission$/);
  await expect(page.locator('#workspace-commission')).toBeVisible();

  await page.getByRole('button', { name: 'Operate' }).click();
  await expect(page).toHaveURL(/\/projects\/alpha\/operate$/);
  await expect(page.locator('#workspace-operate')).toBeVisible();

  await expect(page.getByRole('heading', { name: 'Event Stream' })).toBeVisible();
  await expect(page.locator('#workspace-operate')).toContainText('CONNECTED');
});
