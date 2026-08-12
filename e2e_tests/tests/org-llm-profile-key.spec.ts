import { expect, test, type Page } from "@playwright/test";
import fs from "fs";
import path from "path";

const AUTH_STATE = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({ storageState: AUTH_STATE });

type Organization = {
  id: string;
  is_personal?: boolean;
};

type ProfileSummary = {
  name: string;
  model: string | null;
  api_key_set: boolean;
};

async function requireAdminOrganization(page: Page): Promise<Organization> {
  if (!fs.existsSync(AUTH_STATE)) {
    throw new Error(
      `Missing admin authentication fixture: ${AUTH_STATE}. Create e2e_tests/fixtures/auth.json for an organization admin before running this test.`,
    );
  }

  const organizationsResponse = await page.request.get("/api/organizations");
  expect(organizationsResponse.ok()).toBe(true);
  const organizations = (await organizationsResponse.json()) as {
    items: Organization[];
    current_org_id: string | null;
  };
  const organization = organizations.items.find(
    ({ id }) => id === organizations.current_org_id,
  );
  if (!organization || organization.is_personal) {
    throw new Error(
      "The admin auth fixture must select a non-personal organization.",
    );
  }

  const meResponse = await page.request.get(
    `/api/organizations/${organization.id}/me`,
    { headers: { "X-Org-Id": organization.id } },
  );
  expect(meResponse.ok()).toBe(true);
  const me = (await meResponse.json()) as { role?: string };
  if (me.role !== "admin" && me.role !== "owner") {
    throw new Error(
      `This test requires an organization admin or owner; received role "${me.role ?? "unknown"}".`,
    );
  }

  return organization;
}

test("organization admin can set a profile key and delete the temporary profile", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const apiKey = process.env.TEST_ORG_LLM_API_KEY;
  const model = process.env.TEST_ORG_LLM_MODEL;
  const baseUrl = process.env.TEST_ORG_LLM_BASE_URL;
  if (!apiKey) {
    throw new Error(
      "TEST_ORG_LLM_API_KEY is required and must be a disposable valid provider key.",
    );
  }
  if (!model) {
    throw new Error(
      "TEST_ORG_LLM_MODEL is required in provider/model format for the disposable key.",
    );
  }

  const headers = { "X-Org-Id": organization.id };
  const profilesUrl = `/api/organizations/${organization.id}/profiles`;
  const originalResponse = await page.request.get(profilesUrl, { headers });
  expect(originalResponse.ok()).toBe(true);
  const original = (await originalResponse.json()) as {
    active_profile: string | null;
  };
  if (!original.active_profile) {
    throw new Error(
      "This fixture requires an existing active organization profile so activation can be restored.",
    );
  }

  const temporaryName = `e2e-key-${Date.now()}`;
  const profileUrl = `${profilesUrl}/${encodeURIComponent(temporaryName)}`;
  const activateUrl = (name: string) =>
    `${profilesUrl}/${encodeURIComponent(name)}/activate`;
  let profileCreated = false;

  try {
    const createResponse = await page.request.post(profileUrl, {
      headers,
      data: {
        include_secrets: true,
        llm: {
          model,
          api_key: apiKey,
          ...(baseUrl ? { base_url: baseUrl } : {}),
        },
      },
    });
    expect(createResponse.ok()).toBe(true);
    profileCreated = true;

    const profilesResponse = await page.request.get(profilesUrl, { headers });
    expect(profilesResponse.ok()).toBe(true);
    const profiles = (await profilesResponse.json()) as {
      profiles: ProfileSummary[];
    };
    const temporaryProfile = profiles.profiles.find(
      ({ name }) => name === temporaryName,
    );
    expect(temporaryProfile).toEqual(
      expect.objectContaining({
        name: temporaryName,
        model,
        api_key_set: true,
      }),
    );

    const restoreActiveResponse = await page.request.post(
      activateUrl(original.active_profile),
      { headers },
    );
    expect(restoreActiveResponse.ok()).toBe(true);

    await page.goto("/settings/org-defaults");
    await expect(page.getByText("Available Profiles")).toBeVisible();
    const profileRow = page
      .getByTestId("profile-row")
      .filter({ hasText: temporaryName });
    await expect(profileRow).toContainText(model);
    await profileRow.getByTestId("profile-menu-trigger").click();
    await page.getByTestId("profile-delete").click();

    const deleteResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === profileUrl &&
        response.request().method() === "DELETE",
    );
    await page.getByTestId("delete-profile-confirm").click();
    const deleteResponse = await deleteResponsePromise;
    expect(deleteResponse.ok()).toBe(true);
    profileCreated = false;
    await expect(profileRow).toHaveCount(0);
  } finally {
    if (profileCreated) {
      await page.request.delete(profileUrl, { headers });
    }
    const cleanupResponse = await page.request.post(
      activateUrl(original.active_profile),
      { headers },
    );
    expect
      .soft(
        cleanupResponse.ok(),
        `Failed to restore active organization profile (HTTP ${cleanupResponse.status()}).`,
      )
      .toBe(true);
  }
});
