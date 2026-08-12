import { expect, test, type Locator, type Page } from "@playwright/test";
import path from "path";

const authState = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({ storageState: authState });

interface LlmProfilesResponse {
  profiles: Array<{ name: string }>;
  active_profile: string | null;
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
  const { hostname } = new URL(baseUrl);
  if (hostname === "app.all-hands.dev") {
    throw new Error(
      "This test changes the default LLM profile, so production is not allowed.",
    );
  }
}

function profileRow(page: Page, profileName: string): Locator {
  return page.getByTestId("profile-row").filter({ hasText: profileName });
}

async function fetchProfiles(
  page: Page,
  profilesPath: string,
): Promise<LlmProfilesResponse> {
  const response = await page.request.get(profilesPath);
  if (!response.ok()) {
    throw new Error(
      `LLM profile verification failed with HTTP ${response.status()}.`,
    );
  }
  return response.json() as Promise<LlmProfilesResponse>;
}

async function setDefaultProfile(
  page: Page,
  profilesPath: string,
  profileName: string,
) {
  const row = profileRow(page, profileName);
  await expect(row).toBeVisible();
  const menuTrigger = row.getByTestId("profile-menu-trigger");
  if (!(await menuTrigger.isVisible())) {
    throw new Error(
      `Profile actions for '${profileName}' are unavailable to this member fixture.`,
    );
  }

  const activateResponse = page.waitForResponse((response) => {
    const { pathname } = new URL(response.url());
    return (
      response.request().method() === "POST" &&
      pathname === `${profilesPath}/${encodeURIComponent(profileName)}/activate`
    );
  });
  await menuTrigger.click();
  await page.getByTestId("profile-set-active").click();
  const response = await activateResponse;
  expect(
    response.ok(),
    `setting '${profileName}' as default should succeed`,
  ).toBe(true);
  await expect(row.getByTestId("profile-active-badge")).toBeVisible();
}

test("member sets the default LLM profile", async ({ page }) => {
  test.setTimeout(180_000);
  requireNonProductionTarget();
  const targetProfile = requireEnvironment(
    "TEST_DEFAULT_LLM_PROFILE",
    "Provide an existing org-admin-configured profile that this member can set as default.",
  );
  let changedDefault = false;

  const orgProfilesResponse = page.waitForResponse((response) => {
    const { pathname } = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      /^\/api\/organizations\/[^/]+\/profiles$/.test(pathname)
    );
  });
  await page.goto("/settings/llm");
  const initialResponse = await orgProfilesResponse;
  if (!initialResponse.ok()) {
    throw new Error(
      `Organization profile request failed with HTTP ${initialResponse.status()}.`,
    );
  }
  const profilesPath = new URL(initialResponse.url()).pathname;
  const profiles = (await initialResponse.json()) as LlmProfilesResponse;
  const originalProfile = profiles.active_profile;

  if (!profiles.profiles.some(({ name }) => name === targetProfile)) {
    throw new Error(
      `TEST_DEFAULT_LLM_PROFILE '${targetProfile}' was not visible to the member.`,
    );
  }
  if (!originalProfile) {
    throw new Error(
      "The fixture needs an existing default LLM profile so the test can restore it.",
    );
  }
  if (originalProfile === targetProfile) {
    throw new Error(
      "TEST_DEFAULT_LLM_PROFILE must differ from the current default so the change is exercised.",
    );
  }

  try {
    await setDefaultProfile(page, profilesPath, targetProfile);
    changedDefault = true;
    await expect
      .poll(
        async () =>
          (await fetchProfiles(page, profilesPath || "")).active_profile,
      )
      .toBe(targetProfile);

    await page.reload();
    await expect(
      profileRow(page, targetProfile).getByTestId("profile-active-badge"),
    ).toBeVisible();
  } finally {
    if (changedDefault && profilesPath && originalProfile) {
      const response = await page.request.post(
        `${profilesPath}/${encodeURIComponent(originalProfile)}/activate`,
        { data: {} },
      );
      expect
        .soft(response.ok(), "default profile restoration should succeed")
        .toBe(true);
      await expect
        .poll(
          async () =>
            (await fetchProfiles(page, profilesPath || "")).active_profile,
        )
        .toBe(originalProfile);
    }
  }
});
