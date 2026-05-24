import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";

const apiTarget = process.env.GDLF_API_TARGET || "http://localhost:8080";

export default defineConfig({
  plugins: [TanStackRouterVite(), react()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: false },
      "/ca.pem": { target: apiTarget, changeOrigin: false },
      "/ca/qr": { target: apiTarget, changeOrigin: false },
      "/devices": { target: apiTarget, changeOrigin: false },
      "/healthz": { target: apiTarget, changeOrigin: false },
    },
  },
});
