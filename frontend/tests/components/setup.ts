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
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
