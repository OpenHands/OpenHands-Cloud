import { expect, test } from "@playwright/test";
import path from "node:path";

test.use({
  storageState: path.resolve(import.meta.dirname, "../fixtures/auth.json"),
});

test("authenticated OHE member can navigate through Customize to Skills and Plugins", async ({
  page,
}) => {
  if (!process.env.BASE_URL) {
    throw new Error(
      "BASE_URL is required to target the authenticated OHE deployment",
    );
  }

  await page.goto("/canvas");
  await page.getByTestId("sidebar-skills-link").click();
  await expect(page).toHaveURL(/\/canvas\/mcp\/?$/);
  await expect(page.getByTestId("extensions-navbar-desktop")).toBeVisible();

  const skillsAndPluginsLink = page.getByTestId("sidebar-extensions-/skills");
  await expect(skillsAndPluginsLink).toContainText(/skills and plugins/i);
  const [skillsPage] = await Promise.all([
    page.waitForEvent("popup"),
    skillsAndPluginsLink.click(),
  ]);

  await expect(skillsPage).toHaveURL(/\/settings\/skills\/?$/);
  await expect(skillsPage.getByTestId("skills-settings-screen")).toBeVisible();
});
