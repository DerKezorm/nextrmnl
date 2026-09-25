import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Where the proxy points. It can be redirected via NEXTRMNL_API.
const apiTarget = process.env.NEXTRMNL_API || 'http://127.0.0.1:8460'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // React, Router, xterm and i18next change rarely. In their
        // own file, they stay cached in the browser after an update.
        manualChunks(id) {
          if (id.includes('node_modules')) return 'vendor'
          return undefined
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    css: false,
    exclude: ['node_modules/**', 'dist/**'],
  },
  server: {
    // Fixed port: if it is taken, Vite aborts instead of silently falling back to another one.
    port: 5460,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        // The host stays localhost:5460, so the server recognizes the WebSocket's origin as its own.
        changeOrigin: false,
        ws: true,
      },
    },
  },
})
