import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // The API is the source of truth for everything on screen; proxying in dev
    // keeps the browser on one origin so CORS only has to be right in prod.
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
});
