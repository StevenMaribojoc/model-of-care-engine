import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // Build straight into the backend package so one process serves both the
    // API and the SPA on one origin: one port to run, and no CORS in the
    // packaged app.
    outDir: "../backend/app/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // `npm run dev` only: proxy API calls to uvicorn so the frontend can be
    // developed with hot reload against the real backend.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
