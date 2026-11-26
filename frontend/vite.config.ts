import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the API so the dashboard and FastAPI share an origin.
const api = process.env.OXYGEN_API ?? "http://localhost:8000";
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": api, "/health": api } },
  build: { outDir: "dist", emptyOutDir: true },
});
