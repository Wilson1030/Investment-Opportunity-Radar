import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 本地单用户（D02）：前端只监听本机，并把 /api 代理到本地后端，避免 CORS 配置漂移。
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
})
