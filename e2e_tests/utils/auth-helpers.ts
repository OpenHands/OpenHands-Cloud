import { Page } from "@playwright/test";
import { generate } from "otplib";
import type { GitHubCredentials } from "./config";

/**
 * Shared authentication helpers used by the per-user setup projects.
 *
 * These were extracted from the original global-setup.ts so that both the
 * Returning User and New User setup flows can reuse the same GitHub login,
 * 2FA, OAuth-authorization, device-verification, and TOS-acceptance logic.
 */

/**
 * Check whether the browser is already on an authenticated home screen.
 */
export async function checkIfAuthenticated(page: Page): Promise<boolean> {
  try {
    const homeScreen = page.getByTestId("home-screen");
    const loginPage = page.getByTestId("login-page");

    await page
      .waitForLoadState("networkidle", { timeout: 10_000 })
      .catch(() => {});

    const isOnHome = await homeScreen.isVisible().catch(() => false);
    const isOnLogin = await loginPage.isVisible().catch(() => false);

    return isOnHome && !isOnLogin;
  } catch {
    return false;
  }
}

/**
 * Drive the full GitHub OAuth login flow for a set of credentials.
 *
 * Handles: the "Log in with GitHub" button, GitHub login page, 2FA (when a
 * TOTP secret is provided), OAuth authorization, and device verification.
 */
export async function authenticateWithGitHub(
  page: Page,
  creds: GitHubCredentials,
): Promise<void> {
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
    await handleGithubLoginPage(page, creds.username, creds.password);
  }

  // Handle potential 2FA
  if (creds.totpSecret) {
    await handle2FA(page, creds.totpSecret);
  }

  // Handle OAuth authorization if needed
  await handleOAuthAuthorization(page);

  console.log("GitHub authentication flow completed");
}

/**
 * Handle Terms of Service acceptance flow.
 */
export async function handleTOSAcceptance(page: Page): Promise<void> {
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
 * Handle the GitHub login page (username + password).
 */
async function handleGithubLoginPage(
  page: Page,
  username: string,
  password: string,
): Promise<void> {
  console.log("Reached Github Login Page...");

  const usernameField = page.locator('input[name="login"]');
  const passwordField = page.locator('input[name="password"]');

  await usernameField.waitFor({ state: "visible", timeout: 10_000 });

  await usernameField.fill(username);
  await passwordField.fill(password);

  // Submit the form
  await page.locator('input[type="submit"][value="Sign in"]').click();
}

/**
 * Handle GitHub 2FA if enabled.
 */
async function handle2FA(page: Page, totpSecret: string): Promise<void> {
  // Check if 2FA page appears
  const otpField = page.locator('input[name="app_otp"]');
  const isOtpVisible = await otpField
    .waitFor({ state: "visible", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);

  if (isOtpVisible) {
    console.log("2FA required, generating TOTP code...");

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
 * Handle GitHub device verification if required.
 * GitHub may redirect to /sessions/verified-device in some cases.
 */
async function handleDeviceVerification(page: Page): Promise<void> {
  const currentUrl = page.url();
  if (!currentUrl.includes("sessions/verified-device")) {
    return;
  }

  console.log("Device verification required...");

  const continueButton = page.locator('button[type="submit"]');
  await continueButton.waitFor({ state: "visible", timeout: 10_000 });
  await continueButton.click();
}

/**
 * Generate a TOTP code from a secret.
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
 * Handle the OAuth authorization prompt if it appears.
 */
async function handleOAuthAuthorization(page: Page): Promise<void> {
  // Only check for OAuth authorization if we're on the OAuth authorization page
  const currentUrl = page.url();
  if (!currentUrl.includes("oauth/authorize")) {
    return;
  }

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
