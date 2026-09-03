import { randomUUID } from "node:crypto";
import {
  expect,
  test,
  type APIResponse,
  type Page,
  type Response,
} from "@playwright/test";
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

interface UserSettings {
  git_user_name?: string | null;
  git_user_email?: string | null;
}

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
  if (new URL(baseUrl).hostname === "app.all-hands.dev") {
    throw new Error(
      "This test changes Git identity and creates a repository conversation, so production is not allowed.",
    );
  }
}

async function requireJson<T>(
  response: APIResponse | Response,
  label: string,
): Promise<T> {
  if (!response.ok()) {
    throw new Error(`${label} request failed with HTTP ${response.status()}.`);
  }
  return response.json() as Promise<T>;
}

async function openRepository(
  page: Page,
  provider: string,
  repository: string,
): Promise<void> {
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
  const repositoryOption = page
    .getByTestId("git-repo-dropdown-menu")
    .getByRole("option")
    .filter({ hasText: repository })
    .first();
  await expect(
    repositoryOption,
    `TEST_GIT_IDENTITY_REPOSITORY '${repository}' must be available to the member`,
  ).toBeVisible({ timeout: 60_000 });
  await repositoryOption.click();
  await page.getByTestId("repo-launch-button").click();
  await expect(page.getByTestId("home-git-control-bar-preview")).toContainText(
    repository,
  );
}

test("member Git identity is used by a repository conversation commit", async ({
  page,
}) => {
  test.setTimeout(420_000);
  requireNonProductionTarget();
  const repository = requireEnvironment(
    "TEST_GIT_IDENTITY_REPOSITORY",
    "Provide a readable repository that the conversation may modify locally without pushing.",
  );
  const provider = requireEnvironment(
    "TEST_GIT_IDENTITY_PROVIDER",
    `Provide one of: ${Object.keys(providerLabels).join(", ")}.`,
  );
  const gitUserName = requireEnvironment(
    "TEST_GIT_USERNAME",
    "Provide the unique Git author name to save and verify.",
  );
  const gitUserEmail = requireEnvironment(
    "TEST_GIT_EMAIL",
    "Provide the unique Git author email to save and verify.",
  );
  if (!providerLabels[provider]) {
    throw new Error(
      `TEST_GIT_IDENTITY_PROVIDER must be one of: ${Object.keys(providerLabels).join(", ")}.`,
    );
  }

  const originalSettings = await requireJson<UserSettings>(
    await page.request.get("/api/v1/settings"),
    "settings",
  );
  const marker = randomUUID();
  let conversationId: string | undefined;

  try {
    await page.goto("/settings/app");
    await expect(page.getByTestId("app-settings-screen")).toBeVisible();
    await page.getByTestId("git-user-name-input").fill(gitUserName);
    await page.getByTestId("git-user-email-input").fill(gitUserEmail);

    const saveResponse = page.waitForResponse((response) => {
      const { pathname } = new URL(response.url());
      return (
        response.request().method() === "POST" &&
        pathname.endsWith("/api/v1/settings")
      );
    });
    await page.getByTestId("submit-button").click();
    expect((await saveResponse).ok(), "Git identity save").toBeTruthy();

    await expect
      .poll(async () => {
        const settings = await requireJson<UserSettings>(
          await page.request.get("/api/v1/settings"),
          "saved settings",
        );
        return [settings.git_user_name, settings.git_user_email];
      })
      .toEqual([gitUserName, gitUserEmail]);

    await openRepository(page, provider, repository);
    const prompt = [
      `Create a file named e2e-git-identity-${marker}.txt containing '${marker}'.`,
      "Commit that file locally without pushing.",
      "Then run git log -1 --format='E2E_GIT_AUTHOR=%an|%ae' and reply with that output exactly.",
    ].join(" ");
    await page.getByTestId("chat-input").fill(prompt);
    const createResponse = page.waitForResponse((response) => {
      const { pathname } = new URL(response.url());
      return (
        response.request().method() === "POST" &&
        pathname.endsWith("/api/v1/app-conversations")
      );
    });
    await page.getByTestId("submit-button").click();

    const created = await requireJson<{
      id: string;
      app_conversation_id: string | null;
    }>(await createResponse, "conversation creation");
    conversationId = created.app_conversation_id ?? undefined;
    if (!conversationId) {
      await page.waitForURL(/\/canvas\/conversations\/(?!task-)[^/?#]+/, {
        timeout: 180_000,
      });
      conversationId = new URL(page.url()).pathname.split("/").at(-1);
    }
    expect(conversationId).toBeTruthy();

    const authorMessage = page
      .getByTestId("agent-message")
      .filter({ hasText: "E2E_GIT_AUTHOR=" })
      .last();
    await expect(authorMessage).toBeVisible({ timeout: 240_000 });
    await expect(authorMessage).toContainText(gitUserName);
    await expect(authorMessage).toContainText(gitUserEmail);
  } finally {
    if (conversationId) {
      const response = await page.request.delete(
        `/api/v1/app-conversations/${encodeURIComponent(conversationId)}`,
      );
      expect
        .soft(response.ok(), "conversation cleanup should succeed")
        .toBe(true);
    }
    const restoreResponse = await page.request.post("/api/v1/settings", {
      data: {
        git_user_name: originalSettings.git_user_name ?? "",
        git_user_email: originalSettings.git_user_email ?? "",
      },
    });
    expect.soft(restoreResponse.ok(), "Git identity restoration").toBe(true);
  }
});
