import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => ({
  server: {
    proxy: {
      "/v1": {
        target:
          loadEnv(mode, process.cwd(), "").BACKEND_URL ||
          "http://127.0.0.1:8005",
        changeOrigin: true,
        // Live voice capture streams microphone audio over a WebSocket.
        ws: true,
      },
    },
  },
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    clearMocks: true,
  },
}));
