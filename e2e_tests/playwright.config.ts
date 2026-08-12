import {
  defineConfig,
  devices,
  type ReporterDescription,
} from "@playwright/test";
import dotenv from "dotenv";
import path from "path";

import { getReportPortalReporter } from "./reportportal";

dotenv.config({ path: path.resolve(import.meta.dirname, ".env") });

const authFile = path.resolve(import.meta.dirname, "./fixtures/auth.json");

function getBaseURL(): string {
  const baseUrl = process.env.BASE_URL?.trim();
  if (!baseUrl) {
    throw new Error(
      "BASE_URL is required. Point it to the release environment under test.",
    );
  }
  return new URL(baseUrl).toString();
}

function getReporters(): ReporterDescription[] {
  const reporters: ReporterDescription[] = [
    ["html", { outputFolder: "playwright-report" }],
    ["list"],
  ];
  if (process.env.CI) {
    reporters.push(["github"]);
  }

  const reportPortalReporter = getReportPortalReporter();
  if (reportPortalReporter) {
    reporters.push(reportPortalReporter);
  }
  return reporters;
}

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 1,
  workers: 1,

  // Reporter configuration
  reporter: getReporters(),

  // Timeout configuration
  timeout: 120_000, // 2 minutes per test (agent operations can be slow)
  expect: {
    timeout: 30_000, // 30 seconds for assertions
  },

  // Shared settings for all projects
  use: {
    // Base URL for navigation
    baseURL: getBaseURL(),

    // Collect trace on failure
    trace: "on-first-retry",

    // Screenshots on failure
    screenshot: "only-on-failure",

    // Video recording (useful for debugging CI failures)
    video: process.env.CI ? "on-first-retry" : "off",

    // Ignore SSL errors (for staging/development environments)
    ignoreHTTPSErrors: true,

    storageState: authFile,

    // Browser viewport
    viewport: { width: 1280, height: 720 },

    // Action timeout
    actionTimeout: 15_000,

    // Navigation timeout
    navigationTimeout: 30_000,
  },

  // Define test projects
  projects: [
    // Setup project - handles authentication
    {
      name: "setup",
      testMatch: /global-setup\.ts/,
      use: {
        storageState: undefined, // Don't use existing auth for setup
      },
    },

    // Chromium tests (primary browser)
    {
      name: "chromium",
      use: devices["Desktop Chrome"],
      dependencies: ["setup"],
    },

    // Firefox tests (optional - run with --project=firefox)
    {
      name: "firefox",
      use: {
        ...devices["Desktop Firefox"],
      },
      dependencies: ["setup"],
    },

    // WebKit tests (optional - run with --project=webkit)
    {
      name: "webkit",
      use: {
        ...devices["Desktop Safari"],
      },
      dependencies: ["setup"],
    },
  ],

  // Output directory for test artifacts
  outputDir: "./test-results",

  // Global setup/teardown
  globalSetup: undefined, // We use a setup project instead for better parallelization
  globalTeardown: undefined,
});
