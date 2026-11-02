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
  loginFlow,
  loginWithKeycloakPassword,
} from "../../utils/auth-helpers";
import { fetchWebClientConfig } from "../../utils/billing";
import { ensureRealmUser } from "../../utils/keycloak-admin";

/**
 * New User setup.
 *
 * Makes sure the synthetic Keycloak-native user exists in the realm with this
 * run's password (Admin API), drives Keycloak's local login form as that
 * user, and completes the app's TOS + onboarding flow when the app shows it,
 * so the storage state we save represents that account on the app.
 *
 * The user keeps one Keycloak identity per instance: it is created on the
 * first run and only has its password reset afterwards. The app's OAuth v2
 * login links accounts by the Keycloak ``sub``; a recreated user would arrive
 * with an unknown ``sub`` and be matched by email instead, which the app
 * refuses (409) once more than one account shares the email -- and the legacy
 * login, which keys the app account on the ``sub``, created such a duplicate
 * every time the user was recreated. So the first run against an instance
 * exercises the first-login provisioning (TOS, onboarding form, org
 * bootstrap); later runs sign in to the same account. To exercise first-login
 * again, delete the e2e account through the Super Admin API; the next run
 * recreates it.
 *
 * GitHub is deliberately not involved. The value of the new-user role is
 * exercising the *app's* own sign-in and first-login provisioning (TOS,
 * onboarding form, org bootstrap, first landing on the home screen). Driving
 * real GitHub OAuth for that has repeatedly broken on GitHub-side product
 * changes that are not our regression (2FA reminder page variants,
 * verified-device screen, first-time consent form races); the returning-user
 * role continues to exercise the app ↔ Keycloak ↔ GitHub round-trip on every
 * run.
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

  // Create the synthetic user on the first run against this instance; after
  // that only reset its password, so its Keycloak ``sub`` -- and with it the
  // app account -- stays the same.
  await ensureRealmUser(adminConfig, {
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

  const flow = loginFlow(await fetchWebClientConfig(page.request));
  await loginWithKeycloakPassword(page, creds, flow);
  await completeLoginAndOnboard(page, creds.username);

  await page.context().storageState({ path: authNewUserFile });
  console.log(
    "New user authenticated, state saved to fixtures/auth.new-user.json",
  );
});
