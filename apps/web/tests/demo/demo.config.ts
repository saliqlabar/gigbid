import { defineConfig } from "@playwright/test";

// Records the demo video against the live deployment (not part of the test suite).
// Run: GIGBID_URL=... GIGBID_PASS=... npx playwright test -c tests/demo/demo.config.ts
export default defineConfig({
  testDir: ".",
  timeout: 15 * 60_000,
  workers: 1,
  reporter: [["list"]],
  outputDir: "../../demo-output",
  use: {
    baseURL: process.env.GIGBID_URL ?? "https://gigbid-7w1o.onrender.com",
    viewport: { width: 1280, height: 720 },
    video: { mode: "on", size: { width: 1280, height: 720 } },
    launchOptions: { slowMo: 120 },
  },
});
