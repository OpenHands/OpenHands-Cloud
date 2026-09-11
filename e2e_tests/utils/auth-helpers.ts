import { Page, Locator, expect } from "@playwright/test";
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
function isSettledAppUrl(urlString: string, allowOnboarding: boolean): boolean {
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
  await page.waitForURL((url) => isSettledAppUrl(url.toString(), true), {
    timeout: 60_000,
  });

  // Phase 2: run onboarding steps. TOS and the onboarding form may appear in
  // sequence — loop until we're past both.
  while (isOnboardingUrl(page.url())) {
    await runOnboardingSteps(page, userIdentifier);
  }

  // Phase 3: wait for the final app URL (no intermediate auth hosts, no
  // onboarding pages) and assert the home screen is visible.
  await page.waitForURL((url) => isSettledAppUrl(url.toString(), false), {
    timeout: 60_000,
  });

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
 * Between intermediate steps the form uses React state changes (not URL
 * changes), so we detect step transitions by waiting for the current step's
 * identifying element to disappear rather than waiting for a URL change.
 * Only the final "Finish" click causes a real navigation.
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
    // Only wait for the onboarding form while still on /onboarding. An
    // already-onboarded user may be routed through /onboarding briefly
    // before the app client-side redirects to "/" or "/canvas"; in that
    // case the form never renders, so race the selector wait against that
    // navigation and bail out when the URL moves off /onboarding.
    const onForm = await Promise.race([
      page
        .getByTestId("onboarding-form")
        .waitFor({ state: "visible", timeout: 15_000 })
        .then(() => true)
        .catch(() => false),
      page
        .waitForURL((url) => !url.toString().includes("/onboarding"), {
          timeout: 15_000,
        })
        .then(() => false)
        .catch(() => false),
    ]);

    if (!onForm) {
      console.log("Onboarding form skipped; already onboarded.");
      return;
    }

    // Identify the current step by an element unique to it, so we can detect
    // when React swaps in the next step (the element becomes hidden).
    const orgNameInput = page.getByTestId("form-input-org_name");
    const isOrgNameStep = await orgNameInput.isVisible().catch(() => false);

    /** Element that disappears when this step advances to the next. */
    let stepIdentifier: Locator;
    /** Elements to interact with on the current step. */
    let fillAction: () => Promise<void>;

    if (isOrgNameStep) {
      // Step 1: org name + domain text inputs.
      stepIdentifier = orgNameInput;
      fillAction = async () => {
        await orgNameInput.fill(orgName);
        await page.getByTestId("form-input-org_domain").fill(orgDomain);
      };
    } else {
      // Step 2+: single/multi-select steps. For org size, select "solo".
      // For any remaining select step, pick the first available option.
      const soloOption = page.getByTestId("step-option-solo");
      const isOrgSizeStep = await soloOption.isVisible().catch(() => false);

      if (isOrgSizeStep) {
        stepIdentifier = soloOption;
        fillAction = async () => {
          await soloOption.click();
        };
      } else {
        const firstOption = page
          .getByTestId("step-content")
          .locator('button[data-testid^="step-option-"]')
          .first();
        stepIdentifier = firstOption;
        fillAction = async () => {
          await firstOption.click();
        };
      }
    }

    await fillAction();

    // Click the primary action button (Next or Finish).
    const actionButton = page
      .getByTestId("step-actions")
      .getByRole("button", { name: /^(next|finish)$/i });
    await actionButton.click();

    // Detect step transition without a 30 s URL-wait timeout:
    //  - Intermediate step: React swaps the step content → the step's
    //    identifier element becomes hidden almost instantly.
    //  - Final step: the form submits → real navigation changes the URL
    //    away from /onboarding.
    // Race both signals so whichever fires first resolves immediately.
    await Promise.race([
      stepIdentifier.waitFor({ state: "hidden", timeout: 30_000 }),
      page.waitForURL((url) => !url.toString().includes("/onboarding"), {
        timeout: 30_000,
      }),
    ]).catch(() => {});
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

    // After filling the code, GitHub's JS usually auto-submits the form.
    // Sometimes it does not — in that case we must click the "Verify" button
    // ourselves. Detect the race by waiting briefly for the URL to navigate
    // off the two-factor page; if it doesn't, click Verify. The two-factor
    // page lives at /sessions/two-factor (e.g. /sessions/two-factor/app), so
    // key the wait on that path rather than "authenticate".
    const isStillOn2FAPage = await page
      .waitForURL((url) => !url.toString().includes("/sessions/two-factor"), {
        timeout: 3_000,
      })
      .then(() => false)
      .catch(() => true);

    if (isStillOn2FAPage) {
      console.log("2FA page did not auto-navigate, clicking Verify...");
      await page.getByRole("button", { name: "Verify" }).click();
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

/** URL prefix of GitHub's OAuth authorize page. */
const OAUTH_AUTHORIZE_URL_PREFIX = "https://github.com/login/oauth/authorize";

/** Overall budget for completing the OAuth authorization grant. */
const OAUTH_AUTHORIZE_TIMEOUT_MS = 30_000;

/**
 * Submit the GitHub OAuth authorize form from within the page.
 *
 * The authorize page's Authorize button is kept disabled by GitHub's
 * clickjacking protection (it requires document.hasFocus(), which never
 * resolves in headless Playwright), so we grant access by submitting the
 * authorize form directly. The form carries all the server-rendered hidden
 * fields (client_id, redirect_uri, state, scope); we only set authorize=1.
 *
 * Returns "submitted" when the form was found and submitted, or "missing"
 * when no authorize form is present in the DOM yet. A first-time consent
 * page can render the form a beat after the URL changes, so "missing" is a
 * retryable state — not proof that GitHub already redirected.
 */
async function submitAuthorizeForm(
  page: Page,
): Promise<"submitted" | "missing"> {
  return page.evaluate(() => {
    // Prefer the exact relative action GitHub renders, then fall back to a
    // looser match (absolute action, or any form carrying the authorize
    // control) so a first-time-grant page variant is still handled.
    const form =
      document.querySelector<HTMLFormElement>(
        'form[action="/login/oauth/authorize"]',
      ) ??
      document.querySelector<HTMLFormElement>(
        'form[action*="/login/oauth/authorize"]',
      ) ??
      Array.from(document.querySelectorAll<HTMLFormElement>("form")).find((f) =>
        f.querySelector('button[name="authorize"], input[name="authorize"]'),
      ) ??
      null;
    if (!form) {
      return "missing";
    }
    let input = form.querySelector<HTMLInputElement>('input[name="authorize"]');
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "authorize";
      form.appendChild(input);
    }
    input.value = "1";
    form.submit();
    return "submitted";
  });
}

/**
 * Handle the OAuth authorization prompt if it appears.
 *
 * After 2FA, GitHub either lands on the OAuth authorize page (app not yet
 * authorized for this account — the first-time / new-user path) or redirects
 * straight back to the app (app already authorized — the returning-user
 * path).
 *
 * For a new user whose account has no standing grant, GitHub renders the
 * first-time consent page and the authorize form can appear slightly after
 * the URL settles. The previous implementation submitted the form once and,
 * if it was not yet present, assumed GitHub had "already redirected" and
 * returned — leaving the browser stranded on the authorize page, so the
 * downstream `completeLoginAndOnboard` wait timed out with a misleading
 * stack. Instead, retry until the grant actually completes (the page leaves
 * the authorize URL) and fail loudly with context if it never does.
 */
export async function handleOAuthAuthorization(
  page: Page,
  options: { timeoutMs?: number } = {},
): Promise<void> {
  const timeoutMs = options.timeoutMs ?? OAUTH_AUTHORIZE_TIMEOUT_MS;
  // Wait for navigation to a stable destination: either the OAuth authorize
  // page (app not yet authorized) or a non-GitHub URL (redirected back to
  // the app). Using a positive condition avoids matching intermediate
  // redirect URLs in the GitHub → Keycloak → app chain.
  await page
    .waitForURL(
      (url) => {
        const urlString = url.toString();
        return (
          urlString.startsWith(OAUTH_AUTHORIZE_URL_PREFIX) ||
          !urlString.includes("github.com")
        );
      },
      { timeout: 30_000 },
    )
    .catch(() => {});

  // If we're not on the OAuth authorize page, GitHub skipped it (the app was
  // previously authorized) and redirected straight back to the app.
  if (!page.url().startsWith(OAUTH_AUTHORIZE_URL_PREFIX)) {
    console.log("No OAuth authorization page shown (redirected back to app).");
    return;
  }

  console.log("On OAuth authorization page, granting access...");

  // Retry submitting the authorize form until the grant completes: success is
  // defined as GitHub leaving the authorize URL, not merely "form submitted"
  // (a first-time consent page can re-render, and a submit can race the DOM).
  const deadline = Date.now() + timeoutMs;
  let attempt = 0;
  while (Date.now() < deadline) {
    // Grant is done once GitHub has taken us off the authorize page.
    if (!page.url().startsWith(OAUTH_AUTHORIZE_URL_PREFIX)) {
      console.log("OAuth authorization complete (left authorize page).");
      return;
    }

    attempt += 1;
    let result: "submitted" | "missing" | "navigated";
    try {
      result = await submitAuthorizeForm(page);
    } catch (e) {
      // "Execution context was destroyed" means form.submit() triggered a
      // navigation while page.evaluate was running — the grant went through.
      if (String(e).includes("Execution context was destroyed")) {
        result = "navigated";
      } else {
        throw e;
      }
    }

    if (result === "submitted" || result === "navigated") {
      console.log(`OAuth authorize form submitted (attempt ${attempt}).`);
      // Wait for GitHub to redirect away from the authorize page.
      await page
        .waitForURL(
          (url) => !url.toString().startsWith(OAUTH_AUTHORIZE_URL_PREFIX),
          {
            timeout: 15_000,
          },
        )
        .catch(() => {});
      if (!page.url().startsWith(OAUTH_AUTHORIZE_URL_PREFIX)) {
        console.log("OAuth authorization complete.");
        return;
      }
      // Still on the authorize page — fall through and retry.
    }

    // Form not present yet (or the submit did not take). Give the consent
    // page a moment to finish rendering, then retry.
    await page.waitForTimeout(1_000);
  }

  throw new Error(
    `OAuth authorization did not complete: still on ${page.url()} after ` +
      `${Math.round(timeoutMs / 1000)}s. GitHub did not redirect back to ` +
      "the app after submitting the authorize form (the new-user first-time " +
      "consent page may not have been granted).",
  );
}
