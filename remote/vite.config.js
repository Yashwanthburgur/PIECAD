import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-only convenience: forwards API/WS calls to the FastAPI backend so
// `npm run dev` on :5173 can talk to core/api.py on :8000 without CORS.
// In production the built app is served BY core/api.py itself (same
// origin, see the StaticFiles mount added to core/api.py), so this proxy
// is irrelevant outside local development.
export default defineConfig({
  base: "/remote/",
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
