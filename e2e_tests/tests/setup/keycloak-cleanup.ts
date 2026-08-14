import { test as setup } from "@playwright/test";
import { isUserEnabled, keycloakAdminConfig } from "../../utils/config";
import { deleteNewUsersByEmail } from "../../utils/keycloak-admin";

/**
 * Pre-run cleanup.
 *
 * Runs before the New User setup project. Logs in to Keycloak as the admin and
 * deletes any user whose email matches the New User's email, so the New User
 * gets a fresh account (and a fresh user id) on next login.
 *
 * This is a Node-only step (no browser interaction), but it is implemented as
 * a Playwright project so the harness can order it via project dependencies
 * and report it alongside the rest of the run.
 *
 * Skipped when NEW_GITHUB_USERNAME is unset (no New User role to clean up).
 */
setup("delete new user from keycloak", async () => {
  setup.skip(!isUserEnabled("new-user"), "NEW_GITHUB_USERNAME not set");

  const config = keycloakAdminConfig();
  await deleteNewUsersByEmail(config);
});
