import path from "node:path";
import {
  expect,
  test,
  type APIResponse,
  type Page,
  type Response,
} from "@playwright/test";

const authState = path.resolve(import.meta.dirname, "../fixtures/auth.json");

const providerLabels: Record<string, string> = {
  azure_devops: "Azure DevOps",
  bitbucket: "Bitbucket",
  bitbucket_data_center: "Bitbucket Data Center",
  github: "GitHub",
  gitlab: "GitLab",
};

interface UserSettings {
  git_full_clone?: boolean | null;
}

test.use({ storageState: authState });

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
      "This test changes repository cloning settings and creates a conversation, so production is not allowed.",
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
    `TEST_FULL_HISTORY_REPOSITORY '${repository}' must be available to the member`,
  ).toBeVisible({ timeout: 60_000 });
  await repositoryOption.click();
  await page.getByTestId("repo-launch-button").click();
  await expect(page.getByTestId("home-git-control-bar-preview")).toContainText(
    repository,
  );
}

test("member enables full Git history for a repository conversation and restores it", async ({
  page,
}) => {
  test.setTimeout(420_000);
  requireNonProductionTarget();
  const repository = requireEnvironment(
    "TEST_FULL_HISTORY_REPOSITORY",
    "Provide a readable repository with more than one commit.",
  );
  const provider = requireEnvironment(
    "TEST_FULL_HISTORY_PROVIDER",
    `Provide one of: ${Object.keys(providerLabels).join(", ")}.`,
  );
  const minimumCommitCountText = requireEnvironment(
    "TEST_FULL_HISTORY_MIN_COMMITS",
    "Provide the minimum expected commit count for the configured repository; it must be at least 2.",
  );
  const minimumCommitCount = Number.parseInt(minimumCommitCountText, 10);
  if (!providerLabels[provider]) {
    throw new Error(
      `TEST_FULL_HISTORY_PROVIDER must be one of: ${Object.keys(providerLabels).join(", ")}.`,
    );
  }
  if (!Number.isInteger(minimumCommitCount) || minimumCommitCount < 2) {
    throw new Error(
      "TEST_FULL_HISTORY_MIN_COMMITS must be an integer greater than or equal to 2.",
    );
  }

  const originalSettings = await requireJson<UserSettings>(
    await page.request.get("/api/v1/settings"),
    "settings",
  );
  if (originalSettings.git_full_clone) {
    throw new Error(
      "The authenticated fixture must start with full Git history disabled so this test exercises enabling and restoring it.",
    );
  }
  let conversationId: string | undefined;

  try {
    await page.goto("/settings/app");
    await expect(page.getByTestId("app-settings-screen")).toBeVisible();
    const fullHistoryToggle = page.getByTestId("git-full-clone-switch");
    await expect(fullHistoryToggle).not.toBeChecked();
    await fullHistoryToggle.check();
    await expect(fullHistoryToggle).toBeChecked();

    const saveResponse = page.waitForResponse((response) => {
      const { pathname } = new URL(response.url());
      return (
        response.request().method() === "POST" &&
        pathname.endsWith("/api/v1/settings")
      );
    });
    await page.getByTestId("submit-button").click();
    expect((await saveResponse).ok(), "full Git history save").toBeTruthy();
    await expect
      .poll(async () => {
        const settings = await requireJson<UserSettings>(
          await page.request.get("/api/v1/settings"),
          "saved settings",
        );
        return settings.git_full_clone;
      })
      .toBe(true);

    await openRepository(page, provider, repository);
    await page
      .getByTestId("chat-input")
      .fill(
        "Run git rev-list --count HEAD in the selected repository. Do not infer the result. Reply exactly as E2E_GIT_HISTORY_COUNT=<integer> using the command output.",
      );
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

    const historyMessage = page
      .getByTestId("agent-message")
      .filter({ hasText: "E2E_GIT_HISTORY_COUNT=" })
      .last();
    await expect(historyMessage).toBeVisible({ timeout: 240_000 });
    const match = (await historyMessage.innerText()).match(
      /E2E_GIT_HISTORY_COUNT=(\d+)/,
    );
    expect(match, "agent must report the git rev-list count").toBeTruthy();
    expect(Number(match?.[1])).toBeGreaterThanOrEqual(minimumCommitCount);
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
      data: { git_full_clone: originalSettings.git_full_clone ?? false },
    });
    expect.soft(restoreResponse.ok(), "full history restoration").toBe(true);
  }
});
