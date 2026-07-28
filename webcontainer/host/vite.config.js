import { defineConfig } from 'vite'

// Cross-origin isolation is a hard WebContainer requirement
// (SharedArrayBuffer). Both dev and preview must send these headers.
const isolationHeaders = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
}

export default defineConfig({
  server: { headers: isolationHeaders },
  preview: { headers: isolationHeaders },
})
