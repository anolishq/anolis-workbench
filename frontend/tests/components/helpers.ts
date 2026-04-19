import type { RuntimeStatus, SystemConfig } from '../../src/lib/contracts';

export function createSystemConfig(name = 'demo'): SystemConfig {
  return {
    meta: {
      name,
      created: '2026-01-01T00:00:00Z',
      template: 'sim-quickstart',
    },
    topology: {
      runtime: {
        name,
        http_port: 8080,
        http_bind: '127.0.0.1',
        cors_origins: [],
        cors_allow_credentials: false,
        shutdown_timeout_ms: 5000,
        startup_timeout_ms: 30000,
        polling_interval_ms: 1000,
        log_level: 'info',
        telemetry: { enabled: false },
        automation_enabled: false,
        behavior_tree_path: null,
        providers: [],
      },
      providers: {},
    },
    paths: {
      runtime_executable: '',
      providers: {},
    },
  };
}

export function createRuntimeStatus(overrides: Partial<RuntimeStatus> = {}): RuntimeStatus {
  return {
    running: false,
    active_project: null,
    ...overrides,
  };
}

export function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
    json: async () => payload,
    headers: new Headers(),
    blob: async () => new Blob([JSON.stringify(payload)]),
  } as unknown as Response;
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export function pathFromInput(input: RequestInfo | URL): string {
  const raw =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
  return new URL(raw, 'http://localhost').pathname;
}
