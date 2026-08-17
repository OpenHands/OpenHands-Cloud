import { test, expect } from "@playwright/test";
import { HomePage } from "../pages";
import { runUser } from "../utils/config";

/**
 * Logout specs.
 *
 * Ported from saas_deploy's `e2e_tests/tests/smoke.spec.ts` (the logout +
 * return-to-login flow). This spec destroys the authenticated session, so it
 * must run after every other spec in the project — naming the file
 * `logout.spec.ts` keeps it last under Playwright's default alphabetical file
 * ordering, so no other spec is left holding a torn-down session.
 *
 * As with the rest of the harness, each spec runs once per user role
 * (returning / new-user); the active role is read from project metadata via
 * `runUser(testInfo)`.
 */

test.describe("logout @logout", () => {
  test("should be able to logout and return to login screen", async ({
    page,
    context,
  }, testInfo) => {
    const homePage = new HomePage(page);
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    await homePage.goto();
    await homePage.logout();

    // Verify the login screen is visible — the GitHub login option renders
    // once the redirect back from Keycloak completes.
    const loginSection = page.getByRole("button", {
      name: "Log in with GitHub",
    });
    await expect(loginSection).toBeVisible({ timeout: 10_000 });

    // Verify the keycloak_auth cookie is cleared on logout.
    const cookiesAfter = await context.cookies();
    const keycloakCookieAfter = cookiesAfter.find(
      (c) => c.name === "keycloak_auth",
    );
    console.log(
      `keycloak_auth cookie after logout: ${keycloakCookieAfter ? "present" : "not present"}`,
    );
    expect(keycloakCookieAfter).toBeUndefined();

    await page.screenshot({
      path: "test-results/screenshots/logout-login-screen.png",
    });

    console.log("Logout test passed: session cleared and login screen shown");
  });
});
