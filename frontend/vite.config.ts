import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

/** Dev server proxy: Docker Compose sets this to http://platform-api:9000; local dev defaults to localhost. */
const apiProxyTarget =
  process.env.API_PROXY_TARGET ?? "http://127.0.0.1:9000"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
    watch: {
      usePolling: true,
    },
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
