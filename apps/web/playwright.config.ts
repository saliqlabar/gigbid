import { defineConfig, devices } from "@playwright/test";

// The suite runs against the real API + web build + GigSim with the scripted test model (no GPU, no keys).
export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["html", { open: "never", outputFolder: "playwright-report" }], ["list"]],
  use: { baseURL: "http://localhost:8100", trace: "retain-on-failure", screenshot: "only-on-failure" },
  globalSetup: "./tests/e2e/global-setup.ts",
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] }, grep: /@mobile/ },
  ],
  webServer: [
    { command: "../../scripts/e2e_server.sh gigsim", url: "http://127.0.0.1:8181/health", reuseExistingServer: false, timeout: 60_000 },
    { command: "../../scripts/e2e_server.sh api", url: "http://127.0.0.1:8100/api/health", reuseExistingServer: false, timeout: 120_000 },
    { command: "../../scripts/e2e_server.sh api-degraded", url: "http://127.0.0.1:8101/api/health", reuseExistingServer: false, timeout: 120_000 },
  ],
});
