import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [svelte()],
  resolve: {
    conditions: ['browser'],
  },
  test: {
    include: ['tests/components/**/*.test.ts'],
    environment: 'happy-dom',
    setupFiles: ['tests/components/setup.ts'],
    clearMocks: true,
    restoreMocks: true,
    coverage: {
      enabled: false,
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      reportsDirectory: 'coverage/components',
      include: ['src/**/*.svelte'],
    },
  },
});
