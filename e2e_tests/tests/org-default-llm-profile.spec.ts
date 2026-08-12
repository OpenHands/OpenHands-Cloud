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

test("organization admin can set the default LLM profile and restore it", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const headers = { "X-Org-Id": organization.id };
  const profilesUrl = `/api/organizations/${organization.id}/profiles`;
  const profilesResponse = await page.request.get(profilesUrl, { headers });
  expect(profilesResponse.ok()).toBe(true);
  const profileState = (await profilesResponse.json()) as {
    profiles: ProfileSummary[];
    active_profile: string | null;
  };
  const originalName = profileState.active_profile;
  if (!originalName) {
    throw new Error(
      "This fixture requires an existing active organization LLM profile so it can be restored.",
    );
  }
  const target = profileState.profiles.find(
    ({ name, model }) => name !== originalName && Boolean(model),
  );
  if (!target) {
    throw new Error(
      "This fixture requires a second organization LLM profile with a model configured.",
    );
  }

  const activateUrl = (name: string) =>
    `${profilesUrl}/${encodeURIComponent(name)}/activate`;

  try {
    await page.goto("/settings/org-defaults");
    await expect(page.getByText("Available Profiles")).toBeVisible();

    const targetRow = page
      .getByTestId("profile-row")
      .filter({ hasText: target.name });
    await expect(targetRow).toBeVisible();
    await targetRow.getByTestId("profile-menu-trigger").click();
    const activateResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === activateUrl(target.name) &&
        response.request().method() === "POST",
    );
    await page.getByTestId("profile-set-active").click();
    const activateResponse = await activateResponsePromise;
    expect(activateResponse.ok()).toBe(true);
    await expect(targetRow.getByTestId("profile-active-badge")).toBeVisible();

    const originalRow = page
      .getByTestId("profile-row")
      .filter({ hasText: originalName });
    await originalRow.getByTestId("profile-menu-trigger").click();
    const restoreResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === activateUrl(originalName) &&
        response.request().method() === "POST",
    );
    await page.getByTestId("profile-set-active").click();
    const restoreResponse = await restoreResponsePromise;
    expect(restoreResponse.ok()).toBe(true);
    await expect(originalRow.getByTestId("profile-active-badge")).toBeVisible();
  } finally {
    const cleanupResponse = await page.request.post(activateUrl(originalName), {
      headers,
    });
    expect
      .soft(
        cleanupResponse.ok(),
        `Failed to restore active organization LLM profile (HTTP ${cleanupResponse.status()}).`,
      )
      .toBe(true);
  }
});
