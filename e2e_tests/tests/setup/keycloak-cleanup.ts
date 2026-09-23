import { test as setup } from "@playwright/test";
import {
  assertNotProduction,
  isUserEnabled,
  keycloakAdminConfig,
} from "../../utils/config";
import { deleteNewUsersByEmail } from "../../utils/keycloak-admin";

/**
 * Pre-run cleanup.
 *
 * Runs before the New User setup project. Logs in to Keycloak as the admin and
 * deletes any user whose email matches the New User's email (a reserved
 * e2e-only ``.test`` address, enforced by ``assertE2eOnlyEmail``), so the
 * synthetic New User can be recreated fresh with a known password.
 *
 * This is a Node-only step (no browser interaction), but it is implemented as
 * a Playwright project so the harness can order it via project dependencies
 * and report it alongside the rest of the run.
 *
 * Skipped when ``KEYCLOAK_NEW_USER_USERNAME`` is unset (no New User role to
 * clean up). Refuses to run against production, since deletion in the
 * production realm is only ever destructive.
 */
setup("delete new user from keycloak", async () => {
  setup.skip(!isUserEnabled("new-user"), "KEYCLOAK_NEW_USER_USERNAME not set");
  assertNotProduction();

  const config = keycloakAdminConfig();
  await deleteNewUsersByEmail(config);
});
