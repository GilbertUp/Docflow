import { defineConfig } from "@playwright/test";

import { E2E_PASSWORD } from "./e2e/constants";

/**
 * End-to-end tests drive a real browser against the real API.
 *
 * Locally they use the Edge or Chrome already installed (E2E_CHANNEL=msedge), so nothing large
 * is downloaded. CI installs Playwright's own Chromium.
 */
const python =
  process.env.E2E_PYTHON ?? (process.platform === "win32" ? "..\\.venv\\Scripts\\python.exe" : "python");
const channel = process.env.E2E_CHANNEL || undefined;
const webPort = 5173;
const apiPort = 8000;

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  // The demo organization is shared, so journeys take turns. Each one leaves it as it found it,
  // or uses names of its own.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  outputDir: "./e2e-results",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    ...(channel ? { channel } : {}),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    locale: "en-US",
    timezoneId: "Africa/Kigali",
  },
  webServer: [
    {
      command: `${python} ../backend/scripts/dev_server.py --seed --fresh --data-dir ../.data/e2e --port ${apiPort} --password ${E2E_PASSWORD}`,
      url: `http://127.0.0.1:${apiPort}/health/live`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${webPort} --strictPort`,
      url: `http://127.0.0.1:${webPort}/`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
  ],
});
