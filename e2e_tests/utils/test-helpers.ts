import { Page } from "@playwright/test";

/**
 * Utility functions for integration tests
 */

/**
 * Wait for a condition to be true with polling
 */
export async function waitForCondition(
  condition: () => Promise<boolean>,
  options: {
    timeout?: number;
    interval?: number;
    message?: string;
  } = {},
): Promise<void> {
  const {
    timeout = 30_000,
    interval = 500,
    message = "Condition not met",
  } = options;
  const startTime = Date.now();

  while (Date.now() - startTime < timeout) {
    if (await condition()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }

  throw new Error(`${message} within ${timeout}ms`);
}

/**
 * Retry a function with exponential backoff
 */
export async function retry<T>(
  fn: () => Promise<T>,
  options: {
    maxRetries?: number;
    baseDelay?: number;
    maxDelay?: number;
  } = {},
): Promise<T> {
  const { maxRetries = 3, baseDelay = 1000, maxDelay = 10000 } = options;

  let lastError: Error | undefined;

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error as Error;
      if (attempt < maxRetries - 1) {
        const delay = Math.min(baseDelay * 2 ** attempt, maxDelay);
        console.log(
          `Retry attempt ${attempt + 1}/${maxRetries} after ${delay}ms`,
        );
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  throw lastError;
}

/**
 * Generate a unique test identifier
 */
export function generateTestId(): string {
  return `test-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Log test step with timestamp
 */
export function logStep(step: string): void {
  const timestamp = new Date().toISOString();
  console.log(`[${timestamp}] ${step}`);
}

/**
 * Take a screenshot with a descriptive name
 */
export async function takeScreenshot(
  page: Page,
  name: string,
  options: { fullPage?: boolean } = {},
): Promise<void> {
  const timestamp = Date.now();
  const sanitizedName = name.replace(/[^a-zA-Z0-9-_]/g, "-");
  await page.screenshot({
    path: `test-results/screenshots/${sanitizedName}-${timestamp}.png`,
    fullPage: options.fullPage ?? true,
  });
}

/**
 * Wait for no console errors during an action
 */
export async function expectNoConsoleErrors(
  page: Page,
  action: () => Promise<void>,
): Promise<void> {
  const errors: string[] = [];

  const handler = (msg: import("@playwright/test").ConsoleMessage) => {
    if (msg.type() === "error") {
      const text = msg.text();
      // Filter known acceptable errors
      if (!text.includes("favicon") && !text.includes("sourcemap")) {
        errors.push(text);
      }
    }
  };

  page.on("console", handler);

  try {
    await action();
  } finally {
    page.off("console", handler);
  }

  if (errors.length > 0) {
    throw new Error(`Console errors detected:\n${errors.join("\n")}`);
  }
}

/**
 * Environment helper to get environment-specific values
 */
export const env = {
  baseUrl: process.env.BASE_URL || "https://staging.all-hands.dev",
  testEnv: process.env.TEST_ENV || "staging",
  testRepoUrl:
    process.env.TEST_REPO_URL || "https://github.com/OpenHands/deploy",
  testPrompt: process.env.TEST_PROMPT || "Flip a coin!",
  isCI: process.env.CI === "true",

  getFeatureBranchUrl(branchName: string): string {
    // Sanitize branch name for URL
    const sanitized = branchName.replace(/[^a-zA-Z0-9-]/g, "-").toLowerCase();
    return `https://${sanitized}.staging.all-hands.dev`;
  },
};

/**
 * Check if running in a specific environment
 */
export function isEnvironment(
  env: "staging" | "production" | "local",
): boolean {
  const baseUrl = process.env.BASE_URL || "";

  switch (env) {
    case "staging":
      return baseUrl.includes("staging.all-hands.dev");
    case "production":
      return baseUrl.includes("app.all-hands.dev");
    case "local":
      return baseUrl.includes("localhost");
    default:
      return false;
  }
}

/**
 * Skip test in specific environments
 */
export function skipInEnvironment(
  test: { skip: (condition: boolean, message: string) => void },
  envs: ("staging" | "production" | "local")[],
  reason: string,
): void {
  const shouldSkip = envs.some(isEnvironment);
  test.skip(shouldSkip, `Skipped in ${envs.join(", ")}: ${reason}`);
}
