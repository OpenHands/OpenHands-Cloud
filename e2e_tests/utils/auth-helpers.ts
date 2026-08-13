import { Page, expect } from "@playwright/test";
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
 * URLs that count as "onboarding in progress" — pages the app redirects to
 * for first-time users before they reach the home screen.
 */
const ONBOARDING_PATHS = ["/accept-tos", "/onboarding"];

/** True if the URL is one of the onboarding-intermediate pages. */
function isOnboardingUrl(urlString: string): boolean {
  return ONBOARDING_PATHS.some((p) => urlString.includes(p));
}

/**
 * URL predicate for a "settled" post-redirect destination: either an
 * onboarding-intermediate page (accept-tos, onboarding form) or the final
 * app URL with no intermediate auth/redirect hosts left in the chain.
 */
function isSettledAppUrl(
  urlString: string,
  allowOnboarding: boolean,
): boolean {
  if (allowOnboarding && isOnboardingUrl(urlString)) {
    return true;
  }
  return (
    !urlString.includes("github.com") &&
    !urlString.includes("login") &&
    !urlString.includes("keycloak") &&
    !urlString.includes("sessions/verified-device") &&
    !isOnboardingUrl(urlString)
  );
}

/**
 * Complete the login and onboarding flow after GitHub authentication.
 *
 * Drives the post-OAuth redirect chain (GitHub → Keycloak → app) through any
 * onboarding steps that first-time users encounter — Terms of Service
 * acceptance and the onboarding form (org name + domain) — then asserts the
 * app home screen is visible.
 *
 * Works for both users who have already onboarded (redirect straight to the
 * app) and those who need onboarding (redirect to /accept-tos and/or
 * /onboarding first).
 *
 * @param userIdentifier Used to name the org/domain in the onboarding form
 *   (analytics-only fields). Typically the GitHub username.
 */
export async function completeLoginAndOnboard(
  page: Page,
  userIdentifier?: string,
): Promise<void> {
  // Phase 1: wait for the redirect chain to settle on either an onboarding
  // page (TOS or form) or the final app URL (already onboarded). The
  // onboarding paths are matched explicitly here so the onboarding handlers
  // run before the final-URL wait — otherwise waitForURL would resolve on
  // /accept-tos or /onboarding (neither contains the excluded substrings) and
  // skip the onboarding steps.
  await page.waitForURL(
    (url) => isSettledAppUrl(url.toString(), true),
    { timeout: 60_000 },
  );

  // Phase 2: run onboarding steps. TOS and the onboarding form may appear in
  // sequence — loop until we're past both.
  while (isOnboardingUrl(page.url())) {
    await runOnboardingSteps(page, userIdentifier);
  }

  // Phase 3: wait for the final app URL (no intermediate auth hosts, no
  // onboarding pages) and assert the home screen is visible.
  await page.waitForURL(
    (url) => isSettledAppUrl(url.toString(), false),
    { timeout: 60_000 },
  );

  await expect(page.getByTestId("home-screen")).toBeVisible({
    timeout: 30_000,
  });
}

/**
 * Run the onboarding step the user is currently on.
 *
 * Dispatches to the TOS handler or the onboarding-form handler based on the
 * current URL. Each handler completes its step and waits for the page to
 * navigate to the next destination (another onboarding step or the app).
 */
async function runOnboardingSteps(
  page: Page,
  userIdentifier?: string,
): Promise<void> {
  if (page.url().includes("/accept-tos")) {
    console.log("Onboarding: accepting Terms of Service...");
    await handleTOSAcceptance(page);
    return;
  }

  if (page.url().includes("/onboarding")) {
    await handleOnboardingForm(page, userIdentifier);
    return;
  }
}

/**
 * Fill the onboarding form (org name + domain, then any subsequent steps)
 * and submit it. After submission the app redirects to "/" or "/canvas".
 *
 * The onboarding form for self-hosted deployments has three steps:
 *   1. Org name + domain (text inputs — analytics-only, named after the e2e user)
 *   2. Org size (single select — we pick "solo")
 *   3. Use case (multi select — we pick the first option)
 *
 * Each step is advanced by clicking the Next/Finish button in step-actions.
 */
async function handleOnboardingForm(
  page: Page,
  userIdentifier?: string,
): Promise<void> {
  console.log("Onboarding: filling onboarding form...");

  const orgName = userIdentifier || "openhands-e2e";
  const orgDomain = `${orgName}.e2e.test`;

  // Drive each step until the form submits and the page navigates away from
  // /onboarding (redirects to "/" or "/canvas").
  while (page.url().includes("/onboarding")) {
    await page
      .getByTestId("onboarding-form")
      .waitFor({ state: "visible", timeout: 15_000 });

    // Step 1: org name + domain text inputs.
    const orgNameInput = page.getByTestId("form-input-org_name");
    if (await orgNameInput.isVisible().catch(() => false)) {
      await orgNameInput.fill(orgName);
      await page.getByTestId("form-input-org_domain").fill(orgDomain);
    } else {
      // Step 2+: single/multi-select steps. For org size, select "solo".
      // For any remaining select step, pick the first available option.
      const soloOption = page.getByTestId("step-option-solo");
      if (await soloOption.isVisible().catch(() => false)) {
        await soloOption.click();
      } else {
        const firstOption = page
          .getByTestId("step-content")
          .locator('button[data-testid^="step-option-"]')
          .first();
        if (await firstOption.isVisible().catch(() => false)) {
          await firstOption.click();
        }
      }
    }

    // Click the primary action button (Next or Finish).
    const actionButton = page
      .getByTestId("step-actions")
      .getByRole("button", { name: /^(next|finish)$/i });
    await actionButton.click();

    // Wait for the page to leave /onboarding (form submitted) or advance to
    // the next step.
    await page
      .waitForURL((url) => !url.toString().includes("/onboarding"), {
        timeout: 30_000,
      })
      .catch(() => {});
  }

  console.log("Onboarding form completed.");
}

/**
 * Handle the Terms of Service acceptance dialog if it appears.
 *
 * After OAuth redirects back to the app, first-time users see a dialog with
 * an "I accept the terms of service" checkbox and a Continue button.
 */
export async function handleTOSAcceptance(page: Page): Promise<void> {
  await page
    .waitForLoadState("networkidle", { timeout: 10_000 })
    .catch(() => {});

  // Click the "I accept the terms of service" checkbox
  const tosCheckbox = page.getByRole("checkbox", {
    name: /accept the terms of service/i,
  });
  await tosCheckbox.waitFor({ state: "visible", timeout: 10_000 });
  await tosCheckbox.click();

  // Click the Continue button
  const continueButton = page.getByRole("button", { name: /continue/i });
  await continueButton.click();

  // Wait for redirect away from the TOS page/dialog
  await page.waitForURL((url) => !url.toString().includes("/accept-tos"), {
    timeout: 30_000,
  });
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
 *
 * GitHub rejects TOTP codes that have already been used within the same
 * 30-second window. On Playwright retries (which re-run the whole login flow),
 * the same code would be generated again and rejected. To avoid this, we
 * wait until we're in a fresh TOTP window before generating the code.
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

    // Wait for a fresh TOTP window to avoid reuse errors on retry. TOTP codes
    // are 30 seconds long, so we wait until we're at least 5 seconds into a
    // new window to give plenty of time before it expires.
    await waitForFreshTotpWindow();

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
 * Wait until we're in a fresh TOTP window (at least 5 seconds into a new
 * 30-second period). This prevents "code already used" errors on retries.
 */
async function waitForFreshTotpWindow(): Promise<void> {
  const TOTP_WINDOW_SECONDS = 30;
  const SAFE_OFFSET_SECONDS = 5;
  const now = Date.now();
  const epochSeconds = Math.floor(now / 1000);
  const secondsIntoWindow = epochSeconds % TOTP_WINDOW_SECONDS;

  if (secondsIntoWindow < SAFE_OFFSET_SECONDS) {
    const waitMs = (SAFE_OFFSET_SECONDS - secondsIntoWindow) * 1000;
    console.log(
      `Waiting ${waitMs}ms for a fresh TOTP window (currently ${secondsIntoWindow}s into window)...`,
    );
    await new Promise((resolve) => setTimeout(resolve, waitMs));
  }
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
 *
 * After 2FA, GitHub either lands on the OAuth authorize page (if the app
 * hasn't been authorized yet) or redirects straight back to the app (if it
 * has). The authorize page's Authorize button is kept disabled by GitHub's
 * clickjacking protection, which requires document.hasFocus() — this never
 * resolves in Playwright. So instead of waiting for the button, we submit
 * the form directly via JavaScript.
 */
async function handleOAuthAuthorization(page: Page): Promise<void> {
  // Wait for navigation to a stable destination: either the OAuth authorize
  // page (app not yet authorized) or a non-GitHub URL (redirected back to
  // the app). Using a positive condition avoids matching intermediate
  // redirect URLs in the GitHub → Keycloak → app chain.
  await page
    .waitForURL(
      (url) => {
        const urlString = url.toString();
        return (
          urlString.startsWith("https://github.com/login/oauth/authorize") ||
          !urlString.includes("github.com")
        );
      },
      { timeout: 30_000 },
    )
    .catch(() => {});

  // If we're not on the OAuth authorize page, GitHub skipped it (the app was
  // previously authorized) and redirected straight back to the app.
  const currentUrl = page.url();
  if (!currentUrl.startsWith("https://github.com/login/oauth/authorize")) {
    console.log("No OAuth authorization page shown (redirected back to app).");
    return;
  }

  console.log("On OAuth authorization page, submitting form directly...");

  // Submit the authorize form directly. The form contains all the hidden
  // fields (client_id, redirect_uri, state, scope) server-rendered; we just
  // need to set authorize=1 and submit. This bypasses the disabled button
  // and GitHub's clickjacking protection entirely.
  //
  // GitHub may auto-redirect away from the authorize page at any moment
  // (when the app was recently authorized). In that case the form won't
  // exist or the execution context will be destroyed — both are handled
  // below as a bypass.
  try {
    await page.evaluate(() => {
      const form = document.querySelector<HTMLFormElement>(
        'form[action="/login/oauth/authorize"]',
      );
      if (!form) {
        throw new Error("OAuth authorize form not found on page");
      }
      let input = form.querySelector<HTMLInputElement>(
        'input[name="authorize"]',
      );
      if (!input) {
        input = document.createElement("input");
        input.type = "hidden";
        input.name = "authorize";
        form.appendChild(input);
      }
      input.value = "1";
      form.submit();
    });
  } catch (e) {
    const msg = String(e);
    // "Execution context was destroyed" means form.submit() triggered a
    // navigation — the form was submitted successfully.
    // "form not found" means GitHub already redirected away from the
    // authorize page (bypass) — nothing to do.
    if (
      !msg.includes("Execution context was destroyed") &&
      !msg.includes("form not found")
    ) {
      throw e;
    }
    if (msg.includes("form not found")) {
      console.log("Authorize page bypassed (form not found, already redirected).");
      return;
    }
  }
  console.log("OAuth authorize form submitted.");
}
