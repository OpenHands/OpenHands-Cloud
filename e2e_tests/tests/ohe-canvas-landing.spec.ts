import { expect, test } from "@playwright/test";
import path from "node:path";

test.use({
  storageState: path.resolve(import.meta.dirname, "../fixtures/auth.json"),
});

test("authenticated OHE member can open Agent Canvas", async ({ page }) => {
  if (!process.env.BASE_URL) {
    throw new Error(
      "BASE_URL is required to target the authenticated OHE deployment",
    );
  }

  await page.goto("/canvas");

  await expect(page).toHaveURL(/\/canvas\/?$/);
  await expect(page.getByTestId("home-screen")).toBeVisible();
  await expect(page.getByTestId("home-chat-launcher")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /let.?s start building!?/i }),
  ).toBeVisible();
});
