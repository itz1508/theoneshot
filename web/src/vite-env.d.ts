/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Optional absolute http(s) base URL of the assistant backend.
   * Unset → the app uses the relative /v1/assistant path (Vite proxy in dev).
   */
  readonly VITE_ASSISTANT_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
