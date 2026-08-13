import { test as setup, expect } from "@playwright/test";
import fs from "fs";
import {
  githubCredentialsFor,
  skipAuth,
  authNewUserFile,
} from "../../utils/config";
import {
  authenticateWithGitHub,
  checkIfAuthenticated,
  handleTOSAcceptance,
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

  // A brand-new user is expected to hit the accept-tos page on first login.
  if (page.url().includes("/accept-tos")) {
    await handleTOSAcceptance(page);
  }

  await expect(page.getByTestId("home-screen")).toBeVisible({
    timeout: 30_000,
  });

  await page.context().storageState({ path: authNewUserFile });
  console.log(
    "New user authenticated, state saved to fixtures/auth.new-user.json",
  );
});
