import { expect, test } from "@playwright/test";
import path from "path";

const authState = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({ storageState: authState });

function requireEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(
      `${name} is required. Set BASE_URL to a non-production OHE deployment and ` +
        "TEST_SKILL_REPOSITORY_SOURCE to a readable skill repository source such as github:owner/repo.",
    );
  }
  return value;
}

function requireNonProductionTarget(): void {
  const baseUrl = requireEnvironment("BASE_URL");
  const { hostname } = new URL(baseUrl);
  if (hostname === "app.all-hands.dev") {
    throw new Error(
      "This test creates a skill repository registration and must not run against production.",
    );
  }
}

test("member can add a skill repository from the Skills page", async ({
  page,
}) => {
  requireNonProductionTarget();
  const repositorySource = requireEnvironment("TEST_SKILL_REPOSITORY_SOURCE");
  const marketplaceName = `e2e-member-skills-${Date.now()}`;
  let created = false;

  await page.goto("/settings/skills");
  await expect(page.getByTestId("add-marketplace-button")).toBeVisible();

  try {
    await page.getByTestId("add-marketplace-button").click();
    await page.getByPlaceholder("github:owner/repo").fill(repositorySource);
    await page.getByPlaceholder("e.g., my-skills").fill(marketplaceName);
    await page.getByTestId("marketplace-save-button").click();

    const repositoryRow = page
      .getByRole("row")
      .filter({ hasText: marketplaceName });
    await expect(repositoryRow).toBeVisible();
    await expect(repositoryRow).toContainText(repositorySource);
    created = true;
  } finally {
    if (created) {
      await page
        .getByRole("button", { name: `Delete ${marketplaceName}` })
        .click();
      await page.getByTestId("confirm-delete-button").click();
      await expect(
        page.getByRole("row").filter({ hasText: marketplaceName }),
      ).toHaveCount(0);
    }
  }
});
