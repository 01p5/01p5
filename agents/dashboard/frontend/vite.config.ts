import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Output lives at agents/dashboard/static/dist/ so the Python backend's
// _serve_static (which has static_dir = agents/dashboard/static/dist by
// default after the SPA wiring) can serve it without a separate web
// server. dev server proxies API calls to the local dashboard on :8765.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../static/dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/healthz": "http://127.0.0.1:8765",
      "/tasks": "http://127.0.0.1:8765",
      "/events": { target: "http://127.0.0.1:8765", changeOrigin: true, ws: false },
      "/approvals": "http://127.0.0.1:8765",
      "/audit": "http://127.0.0.1:8765",
      "/tools": "http://127.0.0.1:8765",
      "/stacks": "http://127.0.0.1:8765",
    },
  },
});
