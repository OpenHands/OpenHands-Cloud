import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const authState = path.resolve(import.meta.dirname, "../fixtures/auth.json");

const providerLabels: Record<string, string> = {
  azure_devops: "Azure DevOps",
  bitbucket: "Bitbucket",
  bitbucket_data_center: "Bitbucket Data Center",
  github: "GitHub",
  gitlab: "GitLab",
};

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
      "This test opens configured repository integrations and must not run against production.",
    );
  }
}

async function selectRepository(
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
  const configuredRepository = page
    .getByTestId("git-repo-dropdown-menu")
    .getByRole("option")
    .filter({ hasText: repository })
    .first();
  await expect(
    configuredRepository,
    `TEST_GITHUB_REPOSITORY '${repository}' must be available to the authenticated member`,
  ).toBeVisible({ timeout: 60_000 });
}

test("member verifies GitHub repositories and a GitLab connection from Integrations", async ({
  page,
}) => {
  test.setTimeout(180_000);
  requireNonProductionTarget();
  const githubRepository = requireEnvironment(
    "TEST_GITHUB_REPOSITORY",
    "Provide an owner/repository installed for the deployment's GitHub App and readable by the member.",
  );
  const gitlabStatus = requireEnvironment(
    "TEST_GITLAB_CONNECTION_STATUS",
    "Provide the localized connected-status text expected for the member's configured GitLab connection.",
  );
  let githubConfigurationPage: Page | undefined;

  try {
    await page.goto("/settings/integrations");
    await expect(page.getByTestId("git-settings-screen")).toBeVisible();

    const configureRepositories = page.getByTestId(
      "configure-github-repositories-button",
    );
    await expect(
      configureRepositories,
      "The deployment must expose its GitHub App repository configuration control.",
    ).toBeVisible();

    const popupPromise = page.waitForEvent("popup");
    await configureRepositories.getByRole("button").click();
    githubConfigurationPage = await popupPromise;
    await expect
      .poll(() => githubConfigurationPage?.url() ?? "", {
        message:
          "GitHub repository configuration should open the app installation flow",
      })
      .toMatch(/^https:\/\/github\.com\/apps\/[^/]+\/installations\/new/);

    await page.bringToFront();
    const gitlabConnection = page.getByTestId("gitlab-status-text");
    await expect(
      gitlabConnection,
      "The deployment must enable its GitLab integration section.",
    ).toBeVisible();
    await expect(gitlabConnection).toContainText(gitlabStatus);

    await selectRepository(page, "github", githubRepository);
  } finally {
    await githubConfigurationPage?.close();
  }
});
