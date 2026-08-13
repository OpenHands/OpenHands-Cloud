import { test as setup } from "@playwright/test";
import fs from "fs";
import {
  githubCredentialsFor,
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
 */
setup("authenticate new user", async ({ page, baseURL }) => {
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
