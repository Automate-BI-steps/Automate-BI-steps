import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En développement (npm run dev), l'API est servie par le backend Python (FastAPI, uvicorn api.app:app) sur le port 8000.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});
