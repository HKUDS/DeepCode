import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  timeout: process.env.DEEPCODE_WEB_LIVE === "1" ? 360000 : 90000,
  use: {
    actionTimeout: 10000,
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    launchOptions: process.env.DEEPCODE_CHROME_PATH
      ? { executablePath: process.env.DEEPCODE_CHROME_PATH }
      : {},
  },
});
