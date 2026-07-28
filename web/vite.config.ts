import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // WebContainers (Web Runtime feature) require cross-origin isolation.
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp',
    },
    // Dev-only proxy to the local assistant backend; the client itself
    // only ever targets the relative /v1/assistant path.
    proxy: {
      '/v1/assistant': 'http://127.0.0.1:8799',
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
