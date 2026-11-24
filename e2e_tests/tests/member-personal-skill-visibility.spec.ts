import { expect, test, type BrowserContext } from "@playwright/test";
import fs from "fs";
import path from "path";
import { authNewUserFile, runUser } from "../utils/config";

test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "returning",
    "Requires the stable returning-user fixture.",
  );
});

function requireEnvironment(name: string, guidance: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required. ${guidance}`);
  }
  return value;
}

function requireAuthState(name: string): string {
  const statePath = path.resolve(
    requireEnvironment(
      name,
      "Provide a Playwright storage-state file for a second member of the same OHE organization.",
    ),
  );
  if (!fs.existsSync(statePath)) {
    throw new Error(`${name} does not exist at ${statePath}.`);
  }
  return statePath;
}

function requireNonProductionTarget(): string {
  const baseUrl = requireEnvironment(
    "BASE_URL",
    "Point it to a non-production OHE deployment.",
  );
  const { hostname } = new URL(baseUrl);
  if (hostname === "app.all-hands.dev") {
    throw new Error(
      "This test mutates personal skill settings and must not run against production.",
    );
  }
  return baseUrl;
}

test("personal skill repository is hidden from another organization member", async ({
  browser,
  page,
}) => {
  const baseUrl = requireNonProductionTarget();
  const repositorySource = requireEnvironment(
    "TEST_SKILL_REPOSITORY_SOURCE",
    "Provide a readable skill repository source such as github:owner/repo.",
  );
  const secondaryAuthState = process.env.SECONDARY_AUTH_STATE
    ? requireAuthState("SECONDARY_AUTH_STATE")
    : authNewUserFile;
  const marketplaceName = `e2e-personal-skills-${Date.now()}`;
  let created = false;
  let secondaryContext: BrowserContext | undefined;

  await page.goto("/settings/skills");
  await expect(page.getByTestId("add-marketplace-button")).toBeVisible();

  try {
    await page.getByTestId("add-marketplace-button").click();
    await page.getByPlaceholder("github:owner/repo").fill(repositorySource);
    await page.getByPlaceholder("e.g., my-skills").fill(marketplaceName);

    const scopeSelect = page.getByTestId("marketplace-scope-select");
    if (await scopeSelect.isVisible()) {
      await scopeSelect.selectOption("personal");
    }
    await page.getByTestId("marketplace-save-button").click();

    const primaryRow = page
      .getByRole("row")
      .filter({ hasText: marketplaceName });
    await expect(primaryRow).toBeVisible();
    await expect(primaryRow).toContainText("Personal");
    created = true;

    secondaryContext = await browser.newContext({
      baseURL: baseUrl,
      storageState: secondaryAuthState,
      ignoreHTTPSErrors: true,
    });
    const secondaryPage = await secondaryContext.newPage();
    await secondaryPage.goto("/settings/skills");
    await expect(
      secondaryPage.getByTestId("add-marketplace-button"),
    ).toBeVisible();
    await expect(
      secondaryPage.getByRole("row").filter({ hasText: marketplaceName }),
    ).toHaveCount(0);
  } finally {
    await secondaryContext?.close();
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
