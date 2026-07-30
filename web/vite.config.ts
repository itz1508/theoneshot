import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const assistantBackendTarget = process.env.AUDISOR_ASSISTANT_PROXY_TARGET ?? 'http://127.0.0.1:8803'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    // WebContainers (Web Runtime feature) require cross-origin isolation.
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp',
    },
    // Dev-only proxy to the local assistant backend; the client itself
    // only ever targets the relative /v1/ paths.
    proxy: {
      '/v1/assistant': assistantBackendTarget,
      '/v1/chat': assistantBackendTarget,
      '/v1/operations': assistantBackendTarget,
      '/v1/usage': assistantBackendTarget,
      '/v1/aflow': assistantBackendTarget,
    },
  },
  preview: {
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    css: { modules: { classNameStrategy: 'non-scoped' } },
  },
})
