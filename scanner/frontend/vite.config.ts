import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.TRS_BACKEND ?? "http://localhost:8100";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    proxy: {
      "/api": backend,
      "/ws": { target: backend.replace("http", "ws"), ws: true },
    },
  },
});
