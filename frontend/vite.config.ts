import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const backend =
    loadEnv(mode, process.cwd(), "").BACKEND_URL || "http://127.0.0.1:8005";
  return {
    server: {
      proxy: {
        "/v1": {
          target: backend,
          changeOrigin: true,
          // Live voice capture streams microphone audio over a WebSocket.
          ws: true,
        },
        // Same-origin auth: the refresh cookie is scoped to /auth. The backend
        // checks the browser Origin, which the proxy leaves unchanged.
        "/auth": { target: backend, changeOrigin: true },
      },
    },
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      clearMocks: true,
    },
  };
});
