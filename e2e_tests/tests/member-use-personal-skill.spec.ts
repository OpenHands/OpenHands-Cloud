import { expect, test } from "@playwright/test";
import { runUser } from "../utils/config";

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

function requireNonProductionTarget(): void {
  const baseUrl = requireEnvironment(
    "BASE_URL",
    "Point it to a non-production OHE deployment.",
  );
  const { hostname } = new URL(baseUrl);
  if (hostname === "app.all-hands.dev") {
    throw new Error(
      "This test creates a skill registration and conversation, so production is not allowed.",
    );
  }
}

test("member uses a skill from a personal repository in Canvas", async ({
  page,
}) => {
  test.setTimeout(300_000);
  requireNonProductionTarget();
  const repositorySource = requireEnvironment(
    "TEST_SKILL_REPOSITORY_SOURCE",
    "Provide the source of a readable repository containing the deterministic test skill.",
  );
  const skillName = requireEnvironment(
    "TEST_PERSONAL_SKILL_NAME",
    "Provide the exact skill name exposed by TEST_SKILL_REPOSITORY_SOURCE.",
  );
  const skillPrompt = requireEnvironment(
    "TEST_PERSONAL_SKILL_PROMPT",
    "Provide a prompt that deterministically invokes the configured personal skill.",
  );
  const expectedText = requireEnvironment(
    "TEST_PERSONAL_SKILL_EXPECTED_TEXT",
    "Provide text that only the deterministic skill response emits.",
  );
  const marketplaceName = `e2e-personal-canvas-${Date.now()}`;
  let conversationId: string | undefined;
  let created = false;

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
    await page.getByRole("button", { name: "Auto-Load" }).click();
    await page.getByTestId("marketplace-save-button").click();

    const repositoryRow = page
      .getByRole("row")
      .filter({ hasText: marketplaceName });
    await expect(repositoryRow).toBeVisible();
    await expect(repositoryRow).toContainText("Personal");
    created = true;

    await page.goto("/");
    await expect(page.getByTestId("home-chat-launcher")).toBeVisible();
    await page.getByTestId("chat-input").fill(skillPrompt);
    await page.getByTestId("submit-button").click();
    await page.waitForURL(/\/conversations\/(?!task-)[^/?#]+/, {
      timeout: 180_000,
    });
    conversationId = new URL(page.url()).pathname.split("/").pop();
    expect(conversationId).toBeTruthy();

    await page.getByRole("button", { name: "Tools", exact: true }).click();
    await page.getByTestId("show-skills-button").click();
    const skillsModal = page.getByTestId("skills-modal");
    await expect(skillsModal).toBeVisible();
    await expect(skillsModal.getByText(skillName, { exact: true })).toBeVisible(
      {
        timeout: 60_000,
      },
    );
    await page.getByTestId("close-skills-modal").click();

    await expect(
      page
        .getByTestId("agent-message")
        .filter({ hasText: expectedText })
        .last(),
    ).toBeVisible({ timeout: 180_000 });
  } finally {
    if (conversationId) {
      const response = await page.request.delete(
        `/api/v1/app-conversations/${encodeURIComponent(conversationId)}`,
      );
      expect
        .soft(response.ok(), "conversation cleanup should succeed")
        .toBe(true);
    }
    if (created) {
      await page.goto("/settings/skills");
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
