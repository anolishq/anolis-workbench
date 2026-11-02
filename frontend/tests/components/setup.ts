import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/svelte';
import { afterEach, beforeEach, vi } from 'vitest';

class FakeEventSource {
  url: string;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }

  close(): void {}

  addEventListener(_type: string, _listener: EventListenerOrEventListenerObject): void {}

  removeEventListener(_type: string, _listener: EventListenerOrEventListenerObject): void {}

  dispatchEvent(_event: Event): boolean {
    return true;
  }
}

beforeEach(() => {
  vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
  // No test reaches the network: a component that fetches gets this unless
  // the test stubs fetch itself (Compose resolves pins on mount, #784).
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      throw new Error(`fetch is not stubbed in this test: ${String(input)}`);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
