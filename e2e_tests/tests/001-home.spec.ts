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
 * into the enterprise Settings shell at `/settings`, where the sidebar's
 * user footer is a dropdown trigger (`settings-nav-user-menu`) that reveals
 * a Logout menuitem. We keep the same two assertions ("logged in" +
 * "account menu reachable") but express them in Canvas terms: (1) the
 * Canvas home-screen composer paints, and (2) `/settings` opens and the
 * account dropdown reveals a Logout entry.
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
    // openAccountMenu() navigates to /settings, clicks the user-footer
    // dropdown trigger, and asserts on the revealed Logout menuitem — the
    // concrete equivalent of "hover the avatar and see the account menu"
    // from the pre-Canvas UI. Reaching the menuitem exercises the whole
    // "menu is reachable" guarantee end to end.
    await homePage.openAccountMenu();
    await expect(homePage.settingsScreen).toBeVisible();
    await expect(homePage.logoutMenuItem).toBeVisible();
  });
});
