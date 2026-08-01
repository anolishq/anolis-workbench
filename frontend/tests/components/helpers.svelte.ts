/**
 * Rune-powered test helpers (the .svelte.ts suffix opts this module into the
 * Svelte compiler so $state is available outside components).
 */
export function reactive<T extends object>(value: T): T {
  const state = $state(value);
  return state;
}
