import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { runUser } from "../utils/config";

test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "returning",
    "Requires the stable returning-user fixture.",
  );
});

const providerLabels: Record<string, string> = {
  azure_devops: "Azure DevOps",
  bitbucket: "Bitbucket",
  bitbucket_data_center: "Bitbucket Data Center",
  github: "GitHub",
  gitlab: "GitLab",
};

test("authenticated OHE member can start a Canvas conversation with a repository", async ({
  page,
}) => {
  test.setTimeout(180_000);

  if (!process.env.BASE_URL) {
    throw new Error(
      "BASE_URL is required to target the authenticated OHE deployment",
    );
  }

  const repository = process.env.OHE_E2E_REPOSITORY;
  if (!repository) {
    throw new Error(
      "OHE_E2E_REPOSITORY is required and must be an owner/repository accessible to the authenticated OHE member",
    );
  }

  const provider = process.env.OHE_E2E_GIT_PROVIDER;
  if (!provider || !providerLabels[provider]) {
    throw new Error(
      `OHE_E2E_GIT_PROVIDER is required and must be one of: ${Object.keys(providerLabels).join(", ")}`,
    );
  }

  const prompt = `OHE repository conversation e2e ${randomUUID()}`;
  let createdConversationId: string | undefined;

  try {
    await page.goto("/canvas");
    await page.getByTestId("open-repository-button").click();
    await expect(page.getByTestId("open-repository-dialog-body")).toBeVisible();

    const providerDropdown = page.getByTestId("git-provider-dropdown");
    if ((await providerDropdown.count()) > 0) {
      await providerDropdown.click();
      await page
        .getByRole("option", { name: providerLabels[provider], exact: true })
        .click();
    }

    await page.getByTestId("git-repo-dropdown").fill(repository);
    const repositoryMenu = page.getByTestId("git-repo-dropdown-menu");
    await expect(repositoryMenu).toBeVisible();
    await repositoryMenu
      .getByRole("option")
      .filter({ hasText: repository })
      .first()
      .click();

    await expect(page.getByTestId("repo-launch-button")).toBeEnabled();
    await page.getByTestId("repo-launch-button").click();
    const repositoryPreview = page.getByTestId("home-git-control-bar-preview");
    await expect(repositoryPreview).toContainText(repository);

    await page.getByTestId("chat-input").fill(prompt);
    const createResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/api/v1/app-conversations"),
    );
    await page.getByTestId("submit-button").click();

    const createResponse = await createResponsePromise;
    expect(createResponse.ok()).toBeTruthy();
    const created = (await createResponse.json()) as {
      id: string;
      app_conversation_id: string | null;
    };

    createdConversationId = created.app_conversation_id ?? undefined;
    if (!createdConversationId) {
      await expect(page).toHaveURL(
        /\/canvas\/conversations\/(?!task-)[^/?#]+/,
        { timeout: 120_000 },
      );
      createdConversationId = new URL(page.url()).pathname.split("/").at(-1);
    }
    expect(createdConversationId).toBeTruthy();
  } finally {
    if (createdConversationId) {
      const cleanupResponse = await page.request.delete(
        `/api/v1/app-conversations/${createdConversationId}`,
      );
      expect(cleanupResponse.ok(), "created conversation cleanup").toBeTruthy();
    }
  }
});
