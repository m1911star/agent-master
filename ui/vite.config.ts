import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"

const __dirname = dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": resolve(__dirname, "./src") },
  },
  server: {
    port: 5273,
    strictPort: false,
    proxy: {
      "/api": {
        // Read AGENT_MASTER_PORT for dev convenience; default 8765.
        target: `http://127.0.0.1:${process.env.AGENT_MASTER_PORT ?? "8765"}`,
        changeOrigin: true,
        ws: false,
      },
    },
  },
})
