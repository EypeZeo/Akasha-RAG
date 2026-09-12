import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 版本号单一来源：仓库根 version.txt（由 release-please 维护）。
// 用 node:fs 读取，避开不同 Node 版本对 `import json` 的 assert/with 语法分歧。
const APP_VERSION = (() => {
  try {
    return readFileSync(new URL('../version.txt', import.meta.url), 'utf-8').trim() || '0.0.0'
  } catch {
    return '0.0.0'
  }
})()

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // This desktop app is served from localhost.  Keep route-level splitting
    // only; warning at 500 kB encourages fragile over-splitting for no gain.
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        manualChunks: {
          // Markdown + syntax highlighting is the heaviest dependency in the
          // chat view; keep it in its own long-lived cacheable chunk.
          markdown: ['react-markdown', 'remark-gfm', 'rehype-highlight', 'highlight.js'],
        },
      },
    },
  },
})
