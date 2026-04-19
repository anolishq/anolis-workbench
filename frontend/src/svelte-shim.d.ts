/// <reference types="svelte" />
/// <reference types="vite/client" />

declare module "*.svelte" {
  import type { Component } from "svelte";

  const component: Component<any>;
  export default component;
}

declare global {
  interface Window {
    __ANOLIS_COMPOSER__?: {
      operatorUiBase?: string;
    };
  }
}
