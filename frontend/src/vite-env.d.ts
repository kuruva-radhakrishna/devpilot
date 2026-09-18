/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the DevPilot backend API (set on Vercel; optional locally). */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
