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
 *
 * The old enterprise home rendered a `user-avatar` and a hover-triggered
 * `user-context-menu`. The Canvas home has neither — account controls moved
 * into the enterprise Settings shell at `/settings`. We keep the same two
 * assertions ("logged in" + "account menu reachable") but express them in
 * Canvas terms: (1) the Canvas home-screen composer paints, and (2)
 * `/settings` opens and shows the settings-nav user footer with a Logout
 * button.
 */

test.describe("home screen", () => {
  test("should reach the Canvas home screen after login", async ({
    page,
  }, testInfo) => {
    const homePage = new HomePage(page);
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    await homePage.goto();

    const isLoggedIn = await homePage.isLoggedIn();
    expect(isLoggedIn).toBe(true);

    // The composer is the load-bearing element on the Canvas home; asserting
    // on it confirms the app hydrated past the redirect shell.
    await expect(homePage.chatLauncher).toBeVisible();
  });

  test("should be able to open the account settings screen", async ({
    page,
  }, testInfo) => {
    const homePage = new HomePage(page);
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    await homePage.goto();
    await homePage.openUserMenu();

    await expect(homePage.settingsScreen).toBeVisible();
    // The user footer (with the Logout button) is the concrete equivalent of
    // the old avatar dropdown; assert it renders so the "menu is reachable"
    // guarantee is exercised end to end.
    await expect(page.getByRole("button", { name: /^logout$/i })).toBeVisible();
  });
});
