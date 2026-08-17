import { test as setup } from "@playwright/test";
import fs from "fs";
import {
  githubCredentialsFor,
  isUserEnabled,
  skipAuth,
  authNewUserFile,
} from "../../utils/config";
import {
  authenticateWithGitHub,
  checkIfAuthenticated,
  completeLoginAndOnboard,
} from "../../utils/auth-helpers";

/**
 * New User setup.
 *
 * Depends on the keycloak-cleanup project (see playwright.config.ts), which
 * deletes any existing user matching the New User's email so this login
 * exercises the fresh-account onboarding path.
 *
 * Logs the New User in via GitHub and saves the authenticated storage state to
 * fixtures/auth.new-user.json. When AUTH_METHOD=skip is set and that file
 * already exists, the login is skipped and the existing state is reused.
 *
 * When NEW_GITHUB_USERNAME is unset, this project (and its dependent test
 * projects) are skipped entirely — the run simply doesn't exercise the New
 * User role. This is useful for fresh clusters where there are no pre-existing
 * users to delete and re-onboard.
 */
setup("authenticate new user", async ({ page, baseURL }) => {
  setup.skip(!isUserEnabled("new-user"), "NEW_GITHUB_USERNAME not set");

  if (skipAuth() && fs.existsSync(authNewUserFile)) {
    console.log(
      "Reusing existing New User state from fixtures/auth.new-user.json",
    );
    return;
  }

  await page.goto(baseURL || "/");

  if (await checkIfAuthenticated(page)) {
    console.log("New user already authenticated, saving state...");
    await page.context().storageState({ path: authNewUserFile });
    return;
  }

  const creds = githubCredentialsFor("new-user");
  await authenticateWithGitHub(page, creds);
  await completeLoginAndOnboard(page, creds.username);

  await page.context().storageState({ path: authNewUserFile });
  console.log(
    "New user authenticated, state saved to fixtures/auth.new-user.json",
  );
});
