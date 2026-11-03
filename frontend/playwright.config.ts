import { defineConfig } from "@playwright/test";

// Port 5173 is often taken by other local projects; the demo uses 5180.
const port = Number(process.env.PLAYWRIGHT_PORT ?? 5180);
const mode = process.env.PLAYWRIGHT_DATA_MODE ?? "api";
// "auth" runs the auth smoke test against a real backend and database.
const suite = process.env.PLAYWRIGHT_SUITE;

export default defineConfig({
  testDir: "./e2e",
  testMatch:
    suite === "auth"
      ? "auth.spec.ts"
      : mode === "mock"
        ? "workspace.spec.ts"
        : "qa.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 90000,
  expect: { timeout: 15000 },
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    headless: true,
    channel: process.env.PLAYWRIGHT_CHANNEL,
    // Lets a cached Chromium build be used without downloading a new one.
    launchOptions: process.env.PLAYWRIGHT_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
      : {},
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    env: { VITE_DATA_MODE: mode, VITE_API_BASE_URL: "" },
    command: `npx vite --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
});
