import { test, expect } from "@playwright/test";
import { HomePage } from "../pages";
import { runUser } from "../utils/config";

/**
 * Home screen specs.
 *
 * Ported from saas_deploy's `e2e_tests/tests/smoke.spec.ts` (the avatar and
 * user-menu checks). As with the rest of the harness, each spec runs once per
 * user role (returning / new-user); the active role is read from project
 * metadata via `runUser(testInfo)`.
 */

test.describe("home screen", () => {
  test("should have user avatar visible indicating logged in state", async ({
    page,
  }, testInfo) => {
    const homePage = new HomePage(page);
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    await homePage.goto();

    // Verify the user is logged in and the avatar is visible.
    const isLoggedIn = await homePage.isLoggedIn();
    expect(isLoggedIn).toBe(true);

    await expect(homePage.userAvatar).toBeVisible();
  });

  test("should be able to open the user menu", async ({ page }, testInfo) => {
    const homePage = new HomePage(page);
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    await homePage.goto();

    await homePage.openUserMenu();

    await expect(homePage.accountSettingsMenu).toBeVisible();
  });
});
