import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev: `npm run dev` on :5173 proxies /api to the FastAPI backend on :8000.
// Prod: `npm run build` writes dist/, which FastAPI serves at http://localhost:8000.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
