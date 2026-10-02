import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  worker: { format: 'es' },
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/healthz': 'http://127.0.0.1:8000' } },
})
