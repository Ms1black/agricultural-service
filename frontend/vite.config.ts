import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// npm run dev: API — на бэкенде (по умолчанию http://localhost:8000, запущен с PLANT_INSECURE=1)
export default defineConfig(({ mode }) => {
  const backend = loadEnv(mode, ".", "").BACKEND_URL || "http://localhost:8000";
  return {
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/web/api": backend,
      "/health": backend,
    },
  },
  };
});
