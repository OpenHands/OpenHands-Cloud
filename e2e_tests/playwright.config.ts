import {
  defineConfig,
  devices,
  type ReporterDescription,
} from "@playwright/test";
import dotenv from "dotenv";
import path from "path";

import { getReportPortalReporter } from "./reportportal";

dotenv.config({ path: path.resolve(import.meta.dirname, ".env") });
import {
  authReturningFile,
  authNewUserFile,
  isUserEnabled,
} from "./utils/config";

dotenv.config({ path: path.resolve(import.meta.dirname, ".env") });

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
/**
 * Each user role is opt-in via its `*_GITHUB_USERNAME` env var. When a role is
 * disabled, its paired test projects match no specs (so they don't try to load
 * a storage-state file that was never produced) and the corresponding setup
 * project skips. This lets a run exercise a fresh cluster with no returning
 * user, or vice versa.
 */
const hasReturningUser = isUserEnabled("returning");
const hasNewUser = isUserEnabled("new-user");

/** Match the given file pattern when the role is enabled; match nothing otherwise. */
const matchFor = (enabled: boolean, pattern: RegExp) =>
  enabled ? pattern : /$a/;

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

  // Shared settings for all projects. NOTE: storageState is intentionally NOT
  // set here. Each project sets its own storageState so the setup projects can
  // run with a fresh context and *create* the auth files that the test
  // projects then consume.
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

    // Browser viewport
    viewport: { width: 1280, height: 720 },

    // Action timeout
    actionTimeout: 15_000,

    // Navigation timeout
    navigationTimeout: 30_000,
  },

  // Project topology
  //
  // The suite exercises two user roles over the same specs: "returning" and
  // "new-user". Each role has a setup project that produces a storage-state
  // file; each browser has a paired test project per role that consumes it.
  //
  //   keycloak-cleanup ──▶ setup:new-user
  //   setup:returning
  //
  //   chromium:returning ──▶ setup:returning
  //   chromium:new-user  ──▶ setup:new-user
  //   (and firefox / webkit variants)
  projects: [
    // --- Setup projects -------------------------------------------------

    // Delete the New User from Keycloak so their next login creates a fresh
    // account. Node-only (no browser). Skipped entirely (no matched tests)
    // when NEW_GITHUB_USERNAME is unset.
    {
      name: "keycloak-cleanup",
      testMatch: matchFor(hasNewUser, /setup\/keycloak-cleanup\.ts/),
    },

    // Authenticate the Returning User via GitHub. Matches no tests (so it
    // doesn't launch a browser) when RETURNING_GITHUB_USERNAME is unset.
    {
      name: "setup:returning",
      testMatch: matchFor(hasReturningUser, /setup\/setup-returning\.ts/),
    },

    // Authenticate the New User via GitHub. Depends on keycloak-cleanup so
    // the account is gone before this login runs. Matches no tests when
    // NEW_GITHUB_USERNAME is unset.
    {
      name: "setup:new-user",
      testMatch: matchFor(hasNewUser, /setup\/setup-new-user\.ts/),
      dependencies: ["keycloak-cleanup"],
    },

    // --- Test projects --------------------------------------------------
    //
    // Each spec file (*.spec.ts) is picked up by both the returning and
    // new-user variants of every browser, so the same suite runs once per
    // user role. The active role is exposed to specs via project metadata
    // (`project.metadata.user`); see utils/config.ts#runUser.

    // Chromium (primary browser)
    {
      name: "chromium:returning",
      testMatch: matchFor(hasReturningUser, /.*\.spec\.ts$/),
      testIgnore: /setup\//,
      use: {
        ...devices["Desktop Chrome"],
        storageState: authReturningFile,
      },
      dependencies: ["setup:returning"],
      metadata: { user: "returning" },
    },
    {
      name: "chromium:new-user",
      testMatch: matchFor(hasNewUser, /.*\.spec\.ts$/),
      testIgnore: /setup\//,
      use: {
        ...devices["Desktop Chrome"],
        storageState: authNewUserFile,
      },
      dependencies: ["setup:new-user"],
      metadata: { user: "new-user" },
    },

    // Firefox (optional - run with --project=firefox:*)
    {
      name: "firefox:returning",
      testMatch: matchFor(hasReturningUser, /.*\.spec\.ts$/),
      testIgnore: /setup\//,
      use: {
        ...devices["Desktop Firefox"],
        storageState: authReturningFile,
      },
      dependencies: ["setup:returning"],
      metadata: { user: "returning" },
    },
    {
      name: "firefox:new-user",
      testMatch: matchFor(hasNewUser, /.*\.spec\.ts$/),
      testIgnore: /setup\//,
      use: {
        ...devices["Desktop Firefox"],
        storageState: authNewUserFile,
      },
      dependencies: ["setup:new-user"],
      metadata: { user: "new-user" },
    },

    // WebKit (optional - run with --project=webkit:*)
    {
      name: "webkit:returning",
      testMatch: matchFor(hasReturningUser, /.*\.spec\.ts$/),
      testIgnore: /setup\//,
      use: {
        ...devices["Desktop Safari"],
        storageState: authReturningFile,
      },
      dependencies: ["setup:returning"],
      metadata: { user: "returning" },
    },
    {
      name: "webkit:new-user",
      testMatch: matchFor(hasNewUser, /.*\.spec\.ts$/),
      testIgnore: /setup\//,
      use: {
        ...devices["Desktop Safari"],
        storageState: authNewUserFile,
      },
      dependencies: ["setup:new-user"],
      metadata: { user: "new-user" },
    },
  ],

  // Output directory for test artifacts
  outputDir: "./test-results",

  // Global setup/teardown
  globalSetup: undefined, // We use a setup project instead for better parallelization
  globalTeardown: undefined,
});
