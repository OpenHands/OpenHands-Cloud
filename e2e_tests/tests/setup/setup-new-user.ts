import { test as setup } from "@playwright/test";
import fs from "fs";
import {
  assertNotProduction,
  authNewUserFile,
  isUserEnabled,
  keycloakAdminConfig,
  newUserCredentials,
  skipAuth,
} from "../../utils/config";
import {
  checkIfAuthenticated,
  completeLoginAndOnboard,
  loginWithKeycloakPassword,
} from "../../utils/auth-helpers";
import { createRealmUser } from "../../utils/keycloak-admin";

/**
 * New User setup.
 *
 * Depends on the ``keycloak-cleanup`` project (see ``playwright.config.ts``),
 * which deletes any prior user matching the New User's email. This project
 * then creates a fresh Keycloak-native user via the Admin API, drives
 * Keycloak's local login form as that user, and completes the app's TOS +
 * onboarding flow, so the storage state we save represents a first-login
 * account on the app.
 *
 * GitHub is deliberately not involved. The value of the new-user role is
 * exercising the *app's* first-login provisioning (TOS, onboarding form, org
 * bootstrap, first landing on the home screen). Driving real GitHub OAuth
 * for that has repeatedly broken on GitHub-side product changes that are
 * not our regression (2FA reminder page variants, verified-device screen,
 * first-time consent form races); the returning-user role continues to
 * exercise the app ↔ Keycloak ↔ GitHub round-trip on every run.
 *
 * When ``KEYCLOAK_NEW_USER_USERNAME`` is unset, this project (and its
 * dependent test projects) are skipped entirely. When ``AUTH_METHOD=skip`` is
 * set and the storage-state file already exists, the login is skipped and
 * the existing state is reused.
 */
setup("authenticate new user", async ({ page, baseURL }) => {
  setup.skip(!isUserEnabled("new-user"), "KEYCLOAK_NEW_USER_USERNAME not set");

  if (skipAuth() && fs.existsSync(authNewUserFile)) {
    console.log(
      "Reusing existing New User state from fixtures/auth.new-user.json",
    );
    return;
  }

  // Refuse to run destructive Keycloak realm writes against production.
  assertNotProduction();

  const creds = newUserCredentials();
  const adminConfig = keycloakAdminConfig();

  // Recreate the synthetic user with a known password. The prior
  // ``keycloak-cleanup`` project has already deleted any earlier instance,
  // so this is a fresh create.
  await createRealmUser(adminConfig, {
    email: creds.email,
    username: creds.username,
    password: creds.password,
    firstName: "E2E",
    lastName: "New User",
  });

  await page.goto(baseURL || "/");

  if (await checkIfAuthenticated(page)) {
    console.log("New user already authenticated, saving state...");
    await page.context().storageState({ path: authNewUserFile });
    return;
  }

  await loginWithKeycloakPassword(page, creds);
  await completeLoginAndOnboard(page, creds.username);

  await page.context().storageState({ path: authNewUserFile });
  console.log(
    "New user authenticated, state saved to fixtures/auth.new-user.json",
  );
});
