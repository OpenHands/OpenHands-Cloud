import { test, expect } from "@playwright/test";
import { runUser, env } from "../utils/config";

/**
 * Example spec.
 *
 * Demonstrates how every spec in this suite runs under both the Returning User
 * and the New User without any per-user branching: the `user` is read from
 * Playwright project metadata (see playwright.config.ts) via `runUser()`.
 *
 * The same spec files are pointed at by the paired test projects
 * (chromium:returning, chromium:new-user, ...) so each spec executes once per
 * user role, each with its own authenticated storage state.
 */
test.describe("home screen", () => {
  test("is visible after authentication", async ({ page }, testInfo) => {
    const user = runUser(testInfo);
    test.info().annotations.push({
      type: "user",
      description: user,
    });

    await page.goto("/");

    await expect(page.getByTestId("home-screen")).toBeVisible({
      timeout: 30_000,
    });
  });
});

test.describe("example conversation", () => {
  test("can be started", async ({ page }, testInfo) => {
    const user = runUser(testInfo);
    test.info().annotations.push({
      type: "user",
      description: user,
    });

    await page.goto("/");
    await expect(page.getByTestId("home-screen")).toBeVisible();

    // Placeholder: drive a conversation with env.testRepoUrl / env.testPrompt.
    // Real conversation specs live alongside this file and reuse the same
    // runUser(testInfo) pattern when user-specific assertions are needed.
    expect(env.testRepoUrl).toBeDefined();
    expect(env.testPrompt).toBeDefined();
  });
});
