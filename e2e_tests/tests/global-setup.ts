import { test as setup, expect } from "@playwright/test";
import path from "path";
import fs from "fs";
import { generate } from "otplib";

const authFile = path.join(import.meta.dirname, "../fixtures/auth.json");

/**
 * Global setup test that handles authentication.
 *
 * This test runs before all other tests and saves the authentication state
 * to a file that can be reused across test runs.
 *
 * Authentication Methods:
 * 1. GitHub OAuth (default) - Requires GITHUB_TEST_USERNAME and GITHUB_TEST_PASSWORD
 * 2. Keycloak - Can be customized via KEYCLOAK_* environment variables
 * 3. Pre-existing auth state - If fixtures/auth.json exists and is valid
 *
 * Environment Variables:
 * - AUTH_METHOD: "github" | "keycloak" | "skip" (default: "github")
 * - GITHUB_TEST_USERNAME: GitHub username for test account
 * - GITHUB_TEST_PASSWORD: GitHub password for test account
 * - GITHUB_TEST_TOTP_SECRET: (Optional) TOTP secret for 2FA
 * - KEYCLOAK_URL: Keycloak server URL
 * - KEYCLOAK_USERNAME: Keycloak test username
 * - KEYCLOAK_PASSWORD: Keycloak test password
 */
setup("authenticate", async ({ page, baseURL }) => {
  const authMethod = process.env.AUTH_METHOD || "github";

  // Check if we should skip authentication (use existing auth state)
  if (authMethod === "skip") {
    if (fs.existsSync(authFile)) {
      console.log(
        "Using existing authentication state from fixtures/auth.json",
      );
      return;
    }
    throw new Error(
      "AUTH_METHOD=skip but no existing auth.json found. Please run authentication first.",
    );
  }

  // Navigate to the application
  await page.goto(baseURL || "/");

  // Check if already authenticated
  const isAuthenticated = await checkIfAuthenticated(page);
  if (isAuthenticated) {
    console.log("Already authenticated, saving state...");
    await page.context().storageState({ path: authFile });
    return;
  }

  // Perform authentication based on method
  if (authMethod === "github") {
    await authenticateWithGitHub(page);
  } else if (authMethod === "keycloak") {
    await authenticateWithKeycloak(page);
  } else {
    throw new Error(`Unknown AUTH_METHOD: ${authMethod}`);
  }

  // Wait for successful redirect back to app (could be home page or accept-tos)
  await page.waitForURL(
    (url) => {
      const urlString = url.toString();
      return (
        !urlString.includes("github.com") &&
        !urlString.includes("login") &&
        !urlString.includes("keycloak") &&
        !urlString.includes("sessions/verified-device")
      );
    },
    { timeout: 60_000 },
  );

  // Handle TOS acceptance if redirected to accept-tos page
  if (page.url().includes("/accept-tos")) {
    console.log(
      "Redirected to accept-tos page after authentication, handling TOS acceptance...",
    );
    await handleTOSAcceptance(page);
  }

  // Verify authentication succeeded
  await expect(page.getByTestId("home-screen")).toBeVisible({
    timeout: 30_000,
  });

  // Save authentication state
  await page.context().storageState({ path: authFile });
  console.log("Authentication successful, state saved to fixtures/auth.json");
});

/**
 * Check if the user is already authenticated
 */
async function checkIfAuthenticated(
  page: import("@playwright/test").Page,
): Promise<boolean> {
  try {
    // Look for elements that indicate authentication
    const homeScreen = page.getByTestId("home-screen");
    const loginPage = page.getByTestId("login-page");

    // Wait a bit for the page to stabilize
    await page
      .waitForLoadState("networkidle", { timeout: 10_000 })
      .catch(() => {});

    // Check if we're on the home screen (authenticated)
    const isOnHome = await homeScreen.isVisible().catch(() => false);
    const isOnLogin = await loginPage.isVisible().catch(() => false);

    return isOnHome && !isOnLogin;
  } catch {
    return false;
  }
}

/**
 * Authenticate using GitHub OAuth
 */
async function authenticateWithGitHub(
  page: import("@playwright/test").Page,
): Promise<void> {
  const username = process.env.GITHUB_TEST_USERNAME;
  const password = process.env.GITHUB_TEST_PASSWORD;

  if (!username || !password) {
    throw new Error(
      "GitHub authentication requires GITHUB_TEST_USERNAME and GITHUB_TEST_PASSWORD environment variables",
    );
  }

  console.log("Starting GitHub authentication...");

  // Check if we're already on GitHub (user was redirected because their GitHub
  // session expired while Keycloak session is still valid)
  const currentUrl = page.url();
  if (currentUrl.includes("github.com")) {
    console.log(
      "Already on GitHub page (Keycloak valid, GitHub session expired)",
    );
  } else {
    // Click the GitHub login button
    const githubButton = page.getByRole("button", {
      name: "Log in with GitHub",
    });
    await expect(githubButton).toBeVisible({ timeout: 10_000 });
    await githubButton.click();

    // Wait for redirect - could be GitHub.com, home page, or accept-tos
    // If user is already logged into Keycloak, they may be redirected back to the app
    await page.waitForURL(
      (url) => {
        const urlString = url.toString();
        return (
          urlString.includes("github.com") ||
          urlString.includes("/accept-tos") ||
          // Check if redirected back to home (no login/keycloak in URL)
          (!urlString.includes("keycloak") && !urlString.includes("/login"))
        );
      },
      { timeout: 30_000 },
    );
  }

  // If redirected to accept-tos, handle TOS acceptance
  const urlAfterRedirect = page.url();
  if (urlAfterRedirect.includes("/accept-tos")) {
    console.log("Redirected to accept-tos page, handling TOS acceptance...");
    await handleTOSAcceptance(page);
    console.log("TOS acceptance completed");
    return;
  }

  // If redirected to home page (already authenticated via Keycloak session)
  if (!urlAfterRedirect.includes("github.com")) {
    console.log("Already authenticated via Keycloak session");
    return;
  }

  // Username and password - Github may actually jump over this step but
  // still require 2FA or authorization
  if (urlAfterRedirect.includes("/login?")) {
    await handleGithubLoginPage(page, username, password);
  }

  // Handle potential 2FA
  const totpSecret = process.env.GITHUB_TEST_TOTP_SECRET;
  if (totpSecret) {
    await handle2FA(page, totpSecret);
  }

  // Handle OAuth authorization if needed
  await handleOAuthAuthorization(page);

  console.log("GitHub authentication flow completed");
}

/**
 * Handle Terms of Service acceptance flow
 */
async function handleTOSAcceptance(
  page: import("@playwright/test").Page,
): Promise<void> {
  // Wait for the TOS page to be fully loaded
  await page
    .waitForLoadState("networkidle", { timeout: 10_000 })
    .catch(() => {});

  // Find and click the TOS checkbox
  const tosCheckbox = page.locator('input[type="checkbox"]');
  await tosCheckbox.waitFor({ state: "visible", timeout: 10_000 });
  await tosCheckbox.click();

  // Find and click the Continue button
  const continueButton = page.getByRole("button", { name: "Continue" });
  await expect(continueButton).toBeEnabled({ timeout: 5_000 });
  await continueButton.click();

  // Wait for redirect to home page after TOS acceptance
  await page.waitForURL(
    (url) => {
      const urlString = url.toString();
      return !urlString.includes("/accept-tos");
    },
    { timeout: 30_000 },
  );
}

/**
 * Handle github login page
 * @param page
 * @param totpSecret
 */
async function handleGithubLoginPage(
  page: import("@playwright/test").Page,
  username: string,
  password: string,
): Promise<void> {
  console.log("Reached Github Login Page...");

  // Fill in GitHub credentials
  const usernameField = page.locator('input[name="login"]');
  const passwordField = page.locator('input[name="password"]');

  await usernameField.waitFor({ state: "visible", timeout: 10_000 });

  await usernameField.fill(username);
  await passwordField.fill(password);

  // Submit the form
  await page.locator('input[type="submit"][value="Sign in"]').click();
}

/**
 * Handle GitHub 2FA if enabled
 */
async function handle2FA(
  page: import("@playwright/test").Page,
  totpSecret: string,
): Promise<void> {
  // Check if 2FA page appears
  const otpField = page.locator('input[name="app_otp"]');
  const isOtpVisible = await otpField
    .waitFor({ state: "visible", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);

  if (isOtpVisible) {
    console.log("2FA required, generating TOTP code...");

    // Generate TOTP code
    const totpCode = await generateTOTP(totpSecret);
    await otpField.fill(totpCode);

    // Wait briefly to see if JavaScript auto-submits the form
    // Then check if we're still on the same page before clicking submit
    const isStillOn2FAPage = await page
      .waitForURL((url) => !url.toString().includes("authenticate"), {
        timeout: 3_000,
      })
      .then(() => false)
      .catch(() => true);

    if (isStillOn2FAPage) {
      await page.locator('input[type="submit"][value="Submit"]').click();
    }
  }

  // Handle potential device verification page after 2FA
  await handleDeviceVerification(page);
}

/**
 * Handle GitHub device verification if required
 * GitHub may redirect to /sessions/verified-device in some cases
 */
async function handleDeviceVerification(
  page: import("@playwright/test").Page,
): Promise<void> {
  const currentUrl = page.url();
  if (!currentUrl.includes("sessions/verified-device")) {
    return;
  }

  console.log("Device verification required...");

  // Wait for the page to load and find the continue button
  const continueButton = page.locator('button[type="submit"]');
  await continueButton.waitFor({ state: "visible", timeout: 10_000 });
  await continueButton.click();
}

/**
 * Generate TOTP code from secret
 * Note: In production, use a proper TOTP library like 'otplib'
 */
async function generateTOTP(secret: string): Promise<string> {
  const guardrails = {
    // Had to override this because otp lib insists it is unsafe but github uses it
    MIN_SECRET_BYTES: 10,
    MAX_SECRET_BYTES: 64,
    MIN_PERIOD: 1,
    MAX_PERIOD: 3600,
    MAX_COUNTER: Number.MAX_SAFE_INTEGER,
    MAX_WINDOW: 99,
  };
  const token = await generate({ secret, guardrails });
  return token;
}

/**
 * Handle OAuth authorization prompt if it appears
 */
async function handleOAuthAuthorization(
  page: import("@playwright/test").Page,
): Promise<void> {
  // Only check for OAuth authorization if we're on the OAuth authorization page
  const currentUrl = page.url();
  if (!currentUrl.includes("oauth/authorize")) {
    return;
  }

  // Check if we need to authorize the app
  // Use btn-primary to distinguish from cancel button (both have name="authorize")
  const authorizeButton = page.locator('button.btn-primary[name="authorize"]');
  const isAuthVisible = await authorizeButton
    .isVisible({ timeout: 5_000 })
    .catch(() => false);

  if (isAuthVisible) {
    console.log("OAuth authorization required, clicking authorize...");
    await authorizeButton.click();
  }
}

/**
 * Authenticate using Keycloak
 */
async function authenticateWithKeycloak(
  page: import("@playwright/test").Page,
): Promise<void> {
  const username = process.env.KEYCLOAK_USERNAME;
  const password = process.env.KEYCLOAK_PASSWORD;

  if (!username || !password) {
    throw new Error(
      "Keycloak authentication requires KEYCLOAK_USERNAME and KEYCLOAK_PASSWORD environment variables",
    );
  }

  console.log("Starting Keycloak authentication...");

  // Navigate to login page and initiate Keycloak flow
  // The exact flow depends on your Keycloak configuration
  await page.goto("/login");

  // Wait for Keycloak login page
  await page.waitForURL(/keycloak|auth/, { timeout: 30_000 });

  // Fill in Keycloak credentials
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(password);

  // Submit
  await page.locator("#kc-login").click();

  console.log("Keycloak authentication flow completed");
}
