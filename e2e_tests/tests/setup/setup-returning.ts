import { test as setup } from "@playwright/test";
import fs from "fs";
import {
  githubCredentialsFor,
  skipAuth,
  authReturningFile,
} from "../../utils/config";
import {
  authenticateWithGitHub,
  checkIfAuthenticated,
  completeLoginAndOnboard,
} from "../../utils/auth-helpers";

/**
 * Returning User setup.
 *
 * Logs the Returning User in via GitHub and saves the authenticated storage
 * state to fixtures/auth.returning.json. When AUTH_METHOD=skip is set and that
 * file already exists, the login is skipped and the existing state is reused.
 */
setup("authenticate returning user", async ({ page, baseURL }) => {
  if (skipAuth() && fs.existsSync(authReturningFile)) {
    console.log(
      "Reusing existing Returning User state from fixtures/auth.returning.json",
    );
    return;
  }

  await page.goto(baseURL || "/");

  if (await checkIfAuthenticated(page)) {
    console.log("Returning user already authenticated, saving state...");
    await page.context().storageState({ path: authReturningFile });
    return;
  }

  const creds = githubCredentialsFor("returning");
  await authenticateWithGitHub(page, creds);
  await completeLoginAndOnboard(page, creds.username);

  await page.context().storageState({ path: authReturningFile });
  console.log(
    "Returning user authenticated, state saved to fixtures/auth.returning.json",
  );
});
