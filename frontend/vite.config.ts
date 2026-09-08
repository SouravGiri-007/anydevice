import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In dev, /api requests are proxied to the local Flask backend so the
// frontend never worries about CORS or origins.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // The local Flask backend; override with ANYDEVICE_BACKEND when it
        // runs elsewhere or on a non-default port.
        target: process.env.ANYDEVICE_BACKEND || "http://127.0.0.1:5000",
        changeOrigin: false,
      },
    },
  },
  build: { outDir: "dist" },
});
